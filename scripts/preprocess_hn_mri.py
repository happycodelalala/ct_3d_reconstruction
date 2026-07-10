"""HaN-Seg CT+MRI case -> workstation dataset with BOTH volumes fused in space.

This is the CT+MRI upgrade of the head & neck path. It registers the T1 MR onto
the CT (validated in scripts/register_ct_mr.py), resamples CT, the registered MR,
and a segmentation mask into ONE shared normalized grid, and emits the unified
asset/manifest format — now carrying a second (MR) volume per timepoint so the app
can show CT / MR / fusion.

The shared grid + registered-resampling setup lives in scripts/grid.py (imported here
and by build_envelope_dataset.py); this file is just the single-tumour builder on top.

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
import json
import os

import numpy as np
import SimpleITK as sitk

from asset_common import mesh_from_mask, write_gz  # shared pure asset helpers
from grid import prepare_output_volumes, to_zyx, OUT_XY, CT_HU_LO, CT_HU_HI  # shared grid core
from geometry import warn_if_no_overlap


def build(case_dir, out_dir, ds_id, title, roi_glob, roi_label):
    os.makedirs(out_dir, exist_ok=True)
    print(f"[{ds_id}] loading CT + MR…")
    ref, out_size, out_spacing, _, ct_u8, mr_u8 = prepare_output_volumes(case_dir)

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

    # resample the ROI mask into the shared output grid (CT/MR done in prepare_output_volumes)
    warn_if_no_overlap(man, ref, os.path.basename(cand[0]))
    mask_out = to_zyx(sitk.Resample(man, ref, sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)) > 0
    if mask_out.sum() == 0:
        raise SystemExit("mask empty after resample — check the ROI/params")
    seg = np.where(mask_out, 2, 0).astype(np.uint8)

    phys = np.array([out_size[i] * out_spacing[i] for i in range(3)])
    ext = (phys / phys.max()).tolist()
    tmesh, tv, tf = mesh_from_mask(mask_out, ext)

    # write assets (uint8 volumes ravel Z,Y,X -> x-fastest frontend layout)
    for name, arr in (("ct.bin.gz", ct_u8), ("mri.bin.gz", mr_u8), ("seg.bin.gz", seg)):
        write_gz(os.path.join(out_dir, name), arr)
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
