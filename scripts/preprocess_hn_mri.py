"""HaN-Seg CT+MRI case -> workstation dataset with BOTH volumes fused in space.

This is the CT+MRI upgrade of the head & neck path. It registers the T1 MR onto
the CT (validated in scripts/register_ct_mr.py), resamples CT, the registered MR,
and a segmentation mask into ONE shared normalized grid, and emits the unified
asset/manifest format — now carrying a second (MR) volume per timepoint so the app
can show CT / MR / fusion.

HaN-Seg ships no tumour, so a chosen OAR (default: the mandible) stands in as the
"tumour" label/mesh to exercise the whole CT+MR -> build -> manifest -> fusion path
end to end. Swap the mask for a real tumour segmentation later; nothing else changes.

    .venv/bin/python scripts/preprocess_hn_mri.py --case-dir hanseg_data/HaN-Seg/set_1/case_01

Outputs to public/data/<id>/:
    manifest.json   descriptor (timepoint carries `ct` AND `mri`)
    ct.bin.gz       gzipped uint8 windowed CT   (X*Y*Z)
    mri.bin.gz      gzipped uint8 windowed MR    (same grid, registered)
    seg.bin.gz      gzipped uint8 labels (2 = stand-in tumour)  (same grid)
    tumor.json      isosurface of the mask
    metrics.json    volume / diameters from the native mask
"""
import argparse
import glob
import gzip
import json
import os

import numpy as np
import SimpleITK as sitk
from skimage import measure
from skimage.filters import gaussian

from register_ct_mr import register_cached, load_ct_mr  # validated, cached MR->CT registration

OUT_XY = 256               # in-plane output resolution
OUT_Z_CAP = 220            # cap on output slices
CT_HU_LO, CT_HU_HI = -200, 400   # soft-tissue storage window (H&N); app re-windows on top


# ------------------------------------------------------------------ resampling
def output_grid(ct):
    """A reference grid sharing the CT's physical space, downsampled to
    OUT_XY x OUT_XY x min(Z, cap). Everything is resampled into this grid so the
    CT, MR, mask and mesh all register."""
    sz, sp = ct.GetSize(), ct.GetSpacing()
    oz = int(min(sz[2], OUT_Z_CAP))
    out_size = [OUT_XY, OUT_XY, oz]
    out_spacing = [sp[i] * sz[i] / out_size[i] for i in range(3)]
    ref = sitk.Image(out_size, sitk.sitkFloat32)
    ref.SetSpacing(out_spacing)
    ref.SetOrigin(ct.GetOrigin())
    ref.SetDirection(ct.GetDirection())
    return ref, out_size, out_spacing


def to_zyx(img):
    """SimpleITK image -> numpy (Z, Y, X), which ravels (C-order) to the frontend's
    x-fastest, then y, then slice layout: idx = x + X*(y + Y*z)."""
    return sitk.GetArrayFromImage(img)


def window_ct_u8(a):
    return np.clip((a - CT_HU_LO) / (CT_HU_HI - CT_HU_LO) * 255.0, 0, 255).astype(np.uint8)


def window_mr_u8(a):
    """MR has no standard units; robust-window the non-zero voxels (2..99.5th pct)
    to fill 0..255, so the app's window/level slider has useful range on top."""
    nz = a[a > 0]
    lo, hi = (np.percentile(nz, 2), np.percentile(nz, 99.5)) if nz.size else (0.0, 1.0)
    hi = max(float(hi), float(lo) + 1.0)
    return np.clip((a - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------- meshing
def mesh_from_mask(mask_zyx, ext, sigma=0.6, step=1):
    """Marching-cubes isosurface, vertices mapped into the shared [-ext, +ext] space
    (identical mapping to preprocess_hn.py so mesh and slices register)."""
    sm = gaussian(mask_zyx.astype(np.float32), sigma=sigma)
    v, fc, _, _ = measure.marching_cubes(sm, level=0.5, step_size=step)
    Zd, Yd, Xd = mask_zyx.shape
    ox = ((v[:, 2] / (Xd - 1)) - 0.5) * 2 * ext[0]
    oy = ((v[:, 1] / (Yd - 1)) - 0.5) * 2 * ext[1]
    oz = ((v[:, 0] / (Zd - 1)) - 0.5) * 2 * ext[2]
    return ({"positions": np.stack([ox, oy, oz], 1).round(4).reshape(-1).tolist(),
             "indices": fc.astype(np.int32).reshape(-1).tolist()}, len(v), len(fc))


# --------------------------------------------------------------------- emit
def build(case_dir, out_dir, ds_id, title, roi_glob, roi_label):
    os.makedirs(out_dir, exist_ok=True)
    print(f"[{ds_id}] loading CT + MR…")
    ct, mr = load_ct_mr(case_dir)
    tx = register_cached(ct, mr, os.path.basename(os.path.normpath(case_dir)))

    # stand-in "tumour" mask (an OAR, defined on the CT grid)
    cand = glob.glob(os.path.join(case_dir, roi_glob))
    if not cand:
        raise SystemExit(f"no ROI matching {roi_glob} in {case_dir}")
    man = sitk.ReadImage(cand[0], sitk.sitkUInt8)

    # metrics from the native-resolution mask
    ma = sitk.GetArrayViewFromImage(man)
    sp = man.GetSpacing()
    vox = int((ma > 0).sum())
    nz = np.argwhere(ma > 0)
    bb = nz.max(0) - nz.min(0) + 1  # (dz, dy, dx)
    bbmm = [round(float(bb[2] * sp[0]), 1), round(float(bb[1] * sp[1]), 1), round(float(bb[0] * sp[2]), 1)]
    metrics = {
        "tumorVolumeCm3": round(vox * sp[0] * sp[1] * sp[2] / 1000.0, 1),
        "maxDiameterMm": max(bbmm), "meanDiameterMm": round(sum(bbmm) / 3, 1),
        "tumorVoxels": vox, "bboxMm": bbmm,
    }

    # resample everything into the shared output grid
    ref, out_size, out_spacing = output_grid(ct)
    ct_u8 = window_ct_u8(to_zyx(sitk.Resample(ct, ref, sitk.Transform(), sitk.sitkLinear, 0.0, sitk.sitkFloat32)))
    # MR straight into the output grid via the registration transform (ref shares the
    # CT's physical space) — one interpolation, not a second pass through a full-res
    # CT-grid intermediate.
    mr_u8 = window_mr_u8(to_zyx(sitk.Resample(mr, ref, tx, sitk.sitkLinear, 0.0, sitk.sitkFloat32)))
    mask_out = to_zyx(sitk.Resample(man, ref, sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)) > 0
    if mask_out.sum() == 0:
        raise SystemExit("mask empty after resample — check the ROI/params")
    seg = np.where(mask_out, 2, 0).astype(np.uint8)

    phys = np.array([out_size[i] * out_spacing[i] for i in range(3)])
    ext = (phys / phys.max()).tolist()
    tmesh, tv, tf = mesh_from_mask(mask_out, ext)

    # write assets (uint8 volumes ravel Z,Y,X -> x-fastest frontend layout)
    for name, arr in (("ct.bin.gz", ct_u8), ("mri.bin.gz", mr_u8), ("seg.bin.gz", seg)):
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(gzip.compress(arr.reshape(-1).tobytes(), 6))
    json.dump(tmesh, open(os.path.join(out_dir, "tumor.json"), "w"))
    json.dump(metrics, open(os.path.join(out_dir, "metrics.json"), "w"))

    note = (f"CT + T1 MR (rigid+affine MI registration). '{roi_label}' OAR used as a "
            f"TUMOUR STAND-IN to validate the CT+MR fusion path — not a real lesion.")
    manifest = {
        "id": ds_id, "title": title,
        "source": "HaN-Seg (Podobnik et al., Zenodo 7442914) — CT + T1 MR + OAR masks",
        "modality": "CT + MR · T1",
        "dims": [OUT_XY, OUT_XY, out_size[2]],
        "worldExtent": ext,
        "spacingMm": [round(s, 4) for s in out_spacing],
        "storageWindowHU": {"lo": CT_HU_LO, "hi": CT_HU_HI},
        "defaultWL": {"window": 0.85, "level": 0.5},   # CT
        "mriWL": {"window": 0.9, "level": 0.5},         # MR (already robust-windowed)
        "hasSegmentation": True,
        "labels": {"2": roi_label},
        "clinicalNote": note,
        "timepoints": [{
            "id": "t0", "label": "CT + MR T1",
            "ct": "ct.bin.gz", "mri": "mri.bin.gz", "seg": "seg.bin.gz",
            "tumorMesh": "tumor.json",
        }],
        "meshes": None, "metrics": "metrics.json",
    }
    json.dump(manifest, open(os.path.join(out_dir, "manifest.json"), "w"), indent=2)

    print(f"[{ds_id}] {roi_label}: {metrics['tumorVolumeCm3']} cm³, {tv}v/{tf}f  bbox {bbmm} mm")
    print(f"wrote {out_dir}  dims {[OUT_XY, OUT_XY, out_size[2]]}  extent {[round(x, 3) for x in ext]}")
    print("  run `npm run data:index` to add it to the picker.")


def main():
    ap = argparse.ArgumentParser(description="Build a HaN-Seg CT+MR fusion dataset (OAR as tumour stand-in).")
    ap.add_argument("--case-dir", required=True, help="a HaN-Seg case folder (IMG_CT + IMG_MR_T1 + OAR masks)")
    ap.add_argument("--id", default=None, help="dataset id (default: hanseg_<case>)")
    ap.add_argument("--title", default=None, help="display title")
    ap.add_argument("--roi-glob", default="*OAR_Bone_Mandible*.nrrd", help="glob for the stand-in mask")
    ap.add_argument("--roi-label", default="mandible", help="label shown for the stand-in mask")
    a = ap.parse_args()

    case = os.path.basename(os.path.normpath(a.case_dir))
    ds_id = a.id or f"hanseg_{case}"
    out_dir = os.path.join("public", "data", ds_id)
    build(a.case_dir, out_dir, ds_id, a.title or f"HaN-Seg · {case} (CT+MR)", a.roi_glob, a.roi_label)


if __name__ == "__main__":
    main()
