"""Validate the linchpin step: register a HaN-Seg patient's T1 MR onto its CT.

CT and MR are separate acquisitions in their own geometries, so before any fusion
or cross-modality mask transfer they must be spatially aligned. This script runs a
mutual-information rigid+affine registration (MR -> CT), resamples the MR into the
CT grid, and writes eyeball-able overlays so alignment can be judged on REAL data
before we build the rest of the CT+MRI pipeline around it.

Multimodal intensities don't correspond linearly, so the similarity metric is
Mattes mutual information (NOT sum-of-squares). We keep the transform rigid+affine
(not deformable) so it can't warp anatomy to cheat the metric — see docs.

    .venv/bin/python scripts/register_ct_mr.py --case-dir hanseg_data/HaN-Seg/set_1/case_01

Outputs (under --out, default hanseg_data/registration_check/<case>):
    mr_in_ct.nrrd        MR resampled into the CT grid
    overlay_before.png   CT(green)+MR(magenta) mid-slices, MR at initial alignment
    overlay_after.png    same, after registration  (grey = aligned, colour = mis-)
    checker_after.png    CT/MR checkerboard        (smooth tile seams = aligned)
and prints the Mattes-MI metric before/after (more negative = better).
"""
import argparse
import glob
import json
import os
import numpy as np
import SimpleITK as sitk

from geometry import canonicalize  # reorient CT/MR to a common axis-aligned (LPS) frame


# --------------------------------------------------------------------------- IO
def find_one(case_dir, *needles):
    """First .nrrd in case_dir whose name contains all needles (case-insensitive)."""
    for f in sorted(glob.glob(os.path.join(case_dir, "*.nrrd"))):
        name = os.path.basename(f).lower()
        if all(n.lower() in name for n in needles):
            return f
    return None


def load_ct_mr(case_dir, to_lps=True):
    ct_path = find_one(case_dir, "IMG", "CT") or find_one(case_dir, "CT")
    mr_path = find_one(case_dir, "IMG", "MR") or find_one(case_dir, "MR")
    if not ct_path or not mr_path:
        raise SystemExit(f"could not find CT and MR .nrrd in {case_dir}\n"
                         f"  CT={ct_path}  MR={mr_path}")
    ct = sitk.ReadImage(ct_path, sitk.sitkFloat32)
    mr = sitk.ReadImage(mr_path, sitk.sitkFloat32)
    # Reorient both to LPS so a flipped/axis-permuted acquisition is normalised to the
    # identity frame the pipeline assumes (no-op for already-LPS data; physical points are
    # unchanged, so the cached MR->CT transform stays valid). Oblique volumes are rejected.
    # `to_lps=False` returns the images verbatim — used by the orientation audit, which must
    # report the TRUE source orientation before canonicalization.
    if to_lps:
        ct = canonicalize(ct, f"CT ({os.path.basename(ct_path)})")
        mr = canonicalize(mr, f"MR ({os.path.basename(mr_path)})")
    print(f"  CT {os.path.basename(ct_path)}  size {ct.GetSize()}  spacing "
          f"{tuple(round(s, 2) for s in ct.GetSpacing())}")
    print(f"  MR {os.path.basename(mr_path)}  size {mr.GetSize()}  spacing "
          f"{tuple(round(s, 2) for s in mr.GetSpacing())}")
    return ct, mr


# ------------------------------------------------------------------ registration
def _geom_center(im):
    """Physical-space centre of an image."""
    return im.TransformContinuousIndexToPhysicalPoint([(s - 1) / 2.0 for s in im.GetSize()])


def _mi_evaluator():
    """A coarse Mattes-MI metric for scoring candidate transforms (MetricEvaluate).
    Fixed sampling seed so scores are comparable across the Z search."""
    R = sitk.ImageRegistrationMethod()
    R.SetMetricAsMattesMutualInformation(numberOfHistogramBins=32)
    R.SetMetricSamplingStrategy(R.RANDOM)
    R.SetMetricSamplingPercentage(0.05, seed=1234)
    R.SetInterpolator(sitk.sitkLinear)
    R.SetShrinkFactorsPerLevel([4])
    R.SetSmoothingSigmasPerLevel([2])
    R.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
    return R


def register(fixed, moving):
    """Register moving(MR) onto fixed(CT). Returns (transform, mi_naive, mi_after).

    Robust to HaN-Seg's large FOV/Z mismatch (head-only MR vs head+shoulder CT,
    in different scanner frames): a coarse cranio-caudal search seeds a rigid+affine
    Mattes-MI refinement. Concrete transform types throughout, so no downcasting.
    The returned AffineTransform maps FIXED->MOVING points — ready for Resample.
    """
    # Manual geometry-centre initial (== CenteredTransformInitializer GEOMETRY, but
    # as a concrete Euler3DTransform we can copy and mutate).
    fc, mc = _geom_center(fixed), _geom_center(moving)
    rigid0 = sitk.Euler3DTransform()
    rigid0.SetCenter(fc)
    rigid0.SetTranslation([mc[i] - fc[i] for i in range(3)])

    ev = _mi_evaluator()
    def mi_at(tx):
        ev.SetInitialTransform(tx, inPlace=False)
        return ev.MetricEvaluate(fixed, moving)

    mi_naive = mi_at(rigid0)  # the naive geometry-centre baseline

    # Coarse search over the cranio-caudal (Z) offset — the axis where the two
    # centres disagree and gradient descent's capture range is too small to bridge.
    base_t = list(rigid0.GetTranslation())
    best_dz, best_mi = 0.0, mi_naive
    for dz in range(-150, 151, 10):
        trial = sitk.Euler3DTransform(rigid0)
        trial.SetTranslation([base_t[0], base_t[1], base_t[2] + dz])
        mi = mi_at(trial)
        if mi < best_mi:
            best_mi, best_dz = mi, float(dz)
    rigid0.SetTranslation([base_t[0], base_t[1], base_t[2] + best_dz])
    print(f"    coarse Z search: best Δz = {best_dz:+.0f} mm   (MI {mi_naive:.4f} -> {best_mi:.4f})")

    def make_reg():
        R = sitk.ImageRegistrationMethod()
        R.SetMetricAsMattesMutualInformation(numberOfHistogramBins=50)
        R.SetMetricSamplingStrategy(R.RANDOM)
        R.SetMetricSamplingPercentage(0.10, seed=1234)
        R.SetInterpolator(sitk.sitkLinear)
        # Line search adapts the step each iteration, so the optimiser can't
        # overshoot a good seed into a worse minimum — plain gradient descent
        # diverged on some cases (ended worse than the coarse seed).
        R.SetOptimizerAsGradientDescentLineSearch(
            learningRate=1.0, numberOfIterations=200,
            convergenceMinimumValue=1e-6, convergenceWindowSize=10)
        R.SetOptimizerScalesFromPhysicalShift()
        R.SetShrinkFactorsPerLevel([4, 2, 1])
        R.SetSmoothingSigmasPerLevel([2, 1, 0])
        R.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
        return R

    seed = sitk.Euler3DTransform(rigid0)  # keep the coarse-Z seed as a fallback
    seed_mi = best_mi

    # Stage 1: rigid refine (optimise rigid0 in place, starting from the seeded Z).
    r1 = make_reg()
    r1.SetInitialTransform(rigid0, inPlace=True)
    r1.Execute(fixed, moving)
    rigid = sitk.Euler3DTransform(rigid0)
    rigid_mi = mi_at(rigid)

    # Stage 2: affine refine, initialised from the rigid result.
    affine = sitk.AffineTransform(3)
    affine.SetCenter(rigid.GetCenter())
    affine.SetMatrix(rigid.GetMatrix())
    affine.SetTranslation(rigid.GetTranslation())
    r2 = make_reg()
    r2.SetInitialTransform(affine, inPlace=True)
    r2.Execute(fixed, moving)
    affine_mi = mi_at(affine)

    # Never-regress guard: return whichever stage actually scored best. MI is
    # evaluated on the same fixed-seed coarse metric, so the values compare directly.
    candidates = [("seed", seed_mi, seed), ("rigid", rigid_mi, rigid), ("affine", affine_mi, affine)]
    name, final_mi, final_tx = min(candidates, key=lambda c: c[1])
    print(f"    stage MI: seed {seed_mi:.4f}  rigid {rigid_mi:.4f}  affine {affine_mi:.4f}  -> using '{name}'")
    return final_tx, mi_naive, final_mi


def transform_cache_path(case):
    """Canonical location of a case's MR->CT transform, shared by every script so the
    ~5-min registration is computed once. Small .tfm (the transform only) — NOT a
    resampled volume, so each caller resamples MR into its own grid, one interpolation."""
    return os.path.join("hanseg_data", "registration_check", case, "transform.tfm")


def register_cached(ct, mr, case, verbose=True):
    """register(ct, mr) with a transform cache. Returns the MR->CT transform
    (FIXED->MOVING, ready for sitk.Resample). On a cache miss it registers and writes
    the .tfm; on a hit it reads it back in a millisecond."""
    path = transform_cache_path(case)
    if os.path.exists(path):
        if verbose:
            print(f"[{case}] reusing cached transform {path}")
        return sitk.ReadTransform(path)
    if verbose:
        print(f"[{case}] registering MR -> CT (Mattes MI)…")
    tx, mi0, mi1 = register(ct, mr)
    if verbose:
        print(f"  MI naive {mi0:.4f} -> after {mi1:.4f}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sitk.WriteTransform(tx, path)
    if verbose:
        print(f"  cached transform -> {path}")
    return tx


# ------------------------------------------------------------------- validation
def _to_u8(img):
    """Rescale an intensity volume to uint8 0..255 for display (used for MR)."""
    return sitk.Cast(sitk.RescaleIntensity(img, 0, 255), sitk.sitkUInt8)


def _ct_u8(ct):
    """CT to uint8 via a SOFT-TISSUE window (-160..240 HU) so tissue is visible in
    the overlay. Full-range rescale washes soft tissue out to near-black, leaving
    nothing for the MR to fuse against — which makes a fine registration look bad."""
    return sitk.Cast(sitk.IntensityWindowing(ct, -160.0, 240.0, 0.0, 255.0), sitk.sitkUInt8)


def _rgb_overlay(ct_u8, mr_u8):
    """CT in green, MR in magenta (R+B). Aligned tissue -> grey/white; any
    residual mis-registration shows as green/magenta colour fringes."""
    return sitk.Compose(mr_u8, ct_u8, mr_u8)  # (R=MR, G=CT, B=MR)


def _mid_slices(vol3d):
    """Extract mid axial / coronal / sagittal 2D slices from a 3D image."""
    x, y, z = vol3d.GetSize()
    return [vol3d[:, :, z // 2], vol3d[:, y // 2, :], vol3d[x // 2, :, :]]


def _flip_y(img):
    """Flip the Y axis for radiological display (+Y up) in the QA overlays."""
    return sitk.Flip(img, [False, True])


def write_overlays(fixed_ct, moving_mr, transform, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    ct_u8 = _ct_u8(fixed_ct)

    def mr_resampled(tx):
        mr = sitk.Resample(moving_mr, fixed_ct, tx, sitk.sitkLinear, 0.0, sitk.sitkFloat32)
        return _to_u8(mr)

    identity = sitk.CenteredTransformInitializer(
        fixed_ct, moving_mr, sitk.Euler3DTransform(),
        sitk.CenteredTransformInitializerFilter.GEOMETRY)

    for tag, tx in (("before", identity), ("after", transform)):
        mr_u8 = mr_resampled(tx)
        # tile the three mid-plane overlays side by side into one PNG
        tiles = [_rgb_overlay(c, m) for c, m in zip(_mid_slices(ct_u8), _mid_slices(mr_u8))]
        tiles = [_flip_y(t) for t in tiles]
        sitk.WriteImage(sitk.Tile(tiles, [3, 1], 0), os.path.join(out_dir, f"overlay_{tag}.png"))

    # checkerboard of the registered pair (grey CT vs grey MR)
    mr_u8 = mr_resampled(transform)
    checks = [sitk.CheckerBoard(c, m, [6, 6]) for c, m in
              zip(_mid_slices(ct_u8), _mid_slices(mr_u8))]
    checks = [_flip_y(c) for c in checks]
    sitk.WriteImage(sitk.Tile(checks, [3, 1], 0), os.path.join(out_dir, "checker_after.png"))

    # the registered MR volume itself, for downstream use
    mr_vol = sitk.Resample(moving_mr, fixed_ct, transform, sitk.sitkLinear, 0.0, sitk.sitkFloat32)
    sitk.WriteImage(mr_vol, os.path.join(out_dir, "mr_in_ct.nrrd"))
    return mr_vol


def _mr_slice_u8(sl):
    """Robust (percentile) window of an MR slice, so a few bright voxels don't
    blow the display out to white."""
    a = sitk.GetArrayViewFromImage(sl)
    nz = a[a > 0]
    lo, hi = (float(np.percentile(nz, 2)), float(np.percentile(nz, 99))) if nz.size else (0.0, 1.0)
    hi = max(hi, lo + 1.0)
    return sitk.Cast(sitk.IntensityWindowing(sl, lo, hi, 0.0, 255.0), sitk.sitkUInt8)


def mandible_qa(case_dir, fixed_ct, mr_vol, out_dir):
    """Decisive alignment check: overlay the CT-defined mandible contour on the
    REGISTERED MR. Cortical bone is a signal void on T1, so a good registration
    puts the green jaw contour right on the MR's mandibular outline."""
    cand = glob.glob(os.path.join(case_dir, "*OAR_Bone_Mandible*.nrrd"))
    if not cand:
        print("    (no mandible OAR — skipping contour QA)")
        return
    # OAR segs are cropped to their bounding box, so resample to the CT grid by
    # physical space (identity) to place the mask correctly.
    man = sitk.Resample(sitk.ReadImage(cand[0], sitk.sitkUInt8), fixed_ct,
                        sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)
    zc = int(np.argmax(sitk.GetArrayViewFromImage(man).sum(axis=(1, 2))))  # densest mandible slice
    mr_sl = _flip_y(_mr_slice_u8(mr_vol[:, :, zc]))
    c = sitk.Cast(_flip_y(sitk.BinaryContour(man[:, :, zc], fullyConnected=True)) > 0, sitk.sitkUInt8)
    notc = 1 - c
    rgb = sitk.Compose(mr_sl * notc, mr_sl * notc + c * 255, mr_sl * notc)  # green contour on grey MR
    sitk.WriteImage(rgb, os.path.join(out_dir, "mandible_on_MR.png"))
    print(f"    mandible QA -> mandible_on_MR.png (axial z={zc})")


def main():
    ap = argparse.ArgumentParser(description="Register a HaN-Seg T1 MR onto its CT (MI, rigid+affine).")
    ap.add_argument("--case-dir", required=True, help="a HaN-Seg case folder (contains *IMG_CT*.nrrd + *IMG_MR*.nrrd)")
    ap.add_argument("--out", default=None, help="output dir (default: hanseg_data/registration_check/<case>)")
    a = ap.parse_args()

    case = os.path.basename(os.path.normpath(a.case_dir))
    out_dir = a.out or os.path.join("hanseg_data", "registration_check", case)

    print(f"[{case}] loading…")
    ct, mr = load_ct_mr(a.case_dir)
    print(f"[{case}] registering MR -> CT (Mattes MI, rigid+affine)…")
    transform, mi_naive, mi_after = register(ct, mr)
    print(f"  MI  naive {mi_naive:.4f}   after {mi_after:.4f}   "
          f"(more negative = better; Δ={mi_naive - mi_after:+.4f})")
    # warm the shared transform cache so seed-test / preprocess reuse this registration
    tpath = transform_cache_path(case)
    os.makedirs(os.path.dirname(tpath), exist_ok=True)
    sitk.WriteTransform(transform, tpath)
    print(f"[{case}] writing overlays -> {out_dir}")
    mr_vol = write_overlays(ct, mr, transform, out_dir)
    mandible_qa(a.case_dir, ct, mr_vol, out_dir)
    json.dump({"case": case, "mi_naive": round(mi_naive, 4), "mi_after": round(mi_after, 4)},
              open(os.path.join(out_dir, "qa.json"), "w"))
    print("  done. Inspect overlay_after.png, checker_after.png, mandible_on_MR.png.")


if __name__ == "__main__":
    main()
