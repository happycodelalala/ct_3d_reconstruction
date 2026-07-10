"""Orientation / coordinate-frame guardrails shared across the imaging pipeline.

Every CT, MR and annotation is resampled onto ONE reference grid (grid.output_grid)
whose direction cosines the front-end then IGNORES: it renders volumes,
meshes and MPR planes as a plain axis-aligned index cube (idx = x + X*(y + Y*z), with
world axes assumed X=L/R, Y=A/P, Z=S/I). That is fast and correct — but ONLY while the
reference grid really is an axis-aligned frame. A flipped, axis-permuted or oblique
acquisition would silently mislabel anatomy or de-register mesh-vs-slice with no error.

These helpers close that gap at the point images enter the pipeline:

  * canonicalize()       reorient any image to LPS (ITK-native) so a flipped or
                         axis-permuted acquisition becomes the identity frame the rest
                         of the pipeline assumes. A no-op for already-LPS data — this is
                         the "automatic alignment": voxel order is normalised, physical
                         points are untouched, so cached registrations stay valid.
  * assert_axis_aligned() refuse to proceed on a truly oblique volume (which no reorient
                         can fix without resampling), with an actionable message.
  * warn_if_no_overlap() flag an annotation that lands (near-)empty on the grid after
                         resampling — the classic "the mask doesn't line up" symptom of a
                         frame mismatch, which a silent all-zero resample would hide.

Run as a script to audit one case's frames before building:
    .venv/bin/python scripts/geometry.py --case-dir hanseg_data/HaN-Seg/set_1/case_01
"""
import argparse
import glob
import os

import numpy as np
import SimpleITK as sitk

CANON = "LPS"  # ITK/SimpleITK native anatomical frame -> identity direction cosines


def is_axis_aligned(direction, tol=1e-4):
    """True iff the 3x3 direction cosine matrix is a signed permutation of the identity
    (each patient axis maps to +/- exactly one array axis). This is the ONLY family of
    orientations the index-space front-end and the (x,y,z)<->(k,j,i) spacing/bbox math
    render correctly; anything oblique needs resampling first."""
    D = np.abs(np.asarray(direction, float).reshape(3, 3))
    big = D > 0.5
    return bool(big.sum() == 3 and np.all(big.sum(0) == 1) and np.all(big.sum(1) == 1)
                and np.abs(D[big] - 1.0).max() < tol and (D[~big].max(initial=0.0) < tol))


def orientation_label(img):
    """Anatomical orientation code (e.g. 'LPS', 'RAI') for an image's direction cosines,
    or 'OBLIQUE' if it is not axis-aligned."""
    if not is_axis_aligned(img.GetDirection()):
        return "OBLIQUE"
    f = sitk.DICOMOrientImageFilter()
    return f.GetOrientationFromDirectionCosines(img.GetDirection())


def assert_axis_aligned(img, name="image"):
    """Raise if `img` is oblique (unrepresentable by the index-space front-end)."""
    if not is_axis_aligned(img.GetDirection()):
        D = np.round(np.array(img.GetDirection()).reshape(3, 3), 4)
        raise ValueError(
            f"{name} has an oblique (non-axis-aligned) orientation:\n{D}\n"
            "The front-end renders each volume as an axis-aligned index cube and cannot "
            "represent oblique direction cosines. Resample onto an axis-aligned grid "
            "before building the dataset.")
    return img


def canonicalize(img, name="image"):
    """Reorient to LPS so a flipped/axis-permuted volume becomes the identity frame the
    pipeline assumes. A no-op for already-LPS data. DICOMOrient only permutes/flips axes
    (it does NOT resample), so an oblique volume stays oblique and is rejected here."""
    out = sitk.DICOMOrient(img, CANON)
    return assert_axis_aligned(out, name)


def _extent_bounds(img):
    """(min_xyz, max_xyz) physical corners spanned by an image's voxel grid."""
    sz = img.GetSize()
    c = np.array([img.TransformIndexToPhysicalPoint((i, j, k))
                  for i in (0, sz[0] - 1) for j in (0, sz[1] - 1) for k in (0, sz[2] - 1)])
    return c.min(0), c.max(0)


def _occupied_bounds(mask_img):
    """(min_xyz, max_xyz) physical bbox of a mask's NON-ZERO voxels, or None if empty."""
    a = sitk.GetArrayViewFromImage(mask_img)  # (z, y, x)
    nz = np.argwhere(a > 0)
    if nz.size == 0:
        return None
    lo, hi = nz.min(0)[::-1], nz.max(0)[::-1]  # -> (x, y, z) index order
    c = np.array([mask_img.TransformIndexToPhysicalPoint((int(x), int(y), int(z)))
                  for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    return c.min(0), c.max(0)


def masks_overlap_grid(src_mask, ref, tol=1e-3):
    """True iff the annotation's occupied physical bbox intersects the reference grid's
    physical extent. This is the resolution-INDEPENDENT alignment test: it separates a
    genuine frame mismatch (disjoint physical space) from a small structure merely thinned
    by downsampling (which still overlaps). Empty masks count as 'overlapping' (nothing to
    misplace)."""
    ob = _occupied_bounds(src_mask)
    if ob is None:
        return True
    lo_s, hi_s = ob
    lo_r, hi_r = _extent_bounds(ref)
    return not (np.any(hi_s < lo_r - tol) or np.any(hi_r < lo_s - tol))


def warn_if_no_overlap(src_mask, ref, name="mask"):
    """Warn loudly if a non-empty annotation's physical extent does NOT intersect the
    reference grid — the frame-mismatch symptom (wrong origin/spacing/direction) that a
    silent all-zero resample would otherwise hide as a blank 'no finding' layer. Uses
    physical overlap, so a correctly-placed tiny OAR thinned by downsampling is NOT flagged."""
    if not masks_overlap_grid(src_mask, ref):
        lo_s, hi_s = _occupied_bounds(src_mask)
        lo_r, hi_r = _extent_bounds(ref)
        print(f"  ⚠ {name}: annotation extent {np.round(lo_s, 1)}..{np.round(hi_s, 1)} mm "
              f"lies outside the reference grid {np.round(lo_r, 1)}..{np.round(hi_r, 1)} mm "
              "-- it is NOT aligned with the CT frame.")


# --------------------------------------------------------------------- audit CLI
def _describe(name, img):
    print(f"  {name:22s} size {str(tuple(img.GetSize())):18s} "
          f"spacing {tuple(round(s, 3) for s in img.GetSpacing())}  "
          f"orient {orientation_label(img)}")


def _audit(case_dir):
    from register_ct_mr import load_ct_mr  # local import: avoids a cycle at module load
    print(f"[audit] {case_dir}")
    ct, mr = load_ct_mr(case_dir, to_lps=False)  # RAW, so we report the true source orientation
    print("raw orientations:")
    _describe("CT", ct)
    _describe("MR", mr)
    ct_c, mr_c = canonicalize(ct, "CT"), canonicalize(mr, "MR")
    print(f"after canonicalize -> both {CANON}: "
          f"CT {orientation_label(ct_c)}, MR {orientation_label(mr_c)}  (axis-aligned OK)")

    masks = sorted(glob.glob(os.path.join(case_dir, "*OAR_*.nrrd")))
    print(f"annotations: {len(masks)} OAR mask(s) -- checking physical alignment with the CT grid")
    bad = 0
    for f in masks:
        m = sitk.ReadImage(f, sitk.sitkUInt8)
        if int(sitk.GetArrayViewFromImage(m).astype(bool).sum()) and not masks_overlap_grid(m, ct_c):
            bad += 1
            lo_s, hi_s = _occupied_bounds(m)
            print(f"  ✗ {os.path.basename(f):40s} MISALIGNED  extent "
                  f"{np.round(lo_s, 1)}..{np.round(hi_s, 1)} mm disjoint from CT")
    if bad:
        raise SystemExit(f"{bad} annotation(s) do not align with the CT frame")
    print(f"  ✓ all {len(masks)} annotations are physically aligned with the CT frame")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Audit CT/MR/annotation orientation for a case.")
    ap.add_argument("--case-dir", required=True)
    _audit(ap.parse_args().case_dir)
