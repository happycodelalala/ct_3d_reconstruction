"""Build a multi-label 'envelopes' dataset for the front-end: body + tumour + organ.

Assembles three overlay layers of one HaN-Seg case into ONE multi-label seg volume
(1 = body, 2 = tumour envelope, 3 = organ envelope) plus one isosurface mesh per layer, in
the workstation's manifest format with per-label colours. The front-end renders them as
independently-toggleable layers (2D overlays + 3D shells).

  body   = CT body mask (HU > -500, largest connected component, holes filled)
  tumour = a tumour-envelope NRRD (e.g. scripts/triage_pipeline.py's recall-safe envelope)
  organ  = union of all OAR masks shipped with the case

Reuses the grid / windowing / meshing helpers from preprocess_hn_mri (no duplication).

    .venv/bin/python scripts/build_envelope_dataset.py \
        --case-dir hanseg_data/HaN-Seg/set_1/case_01 \
        --tumour runs/triage/case_01_brainstem_mr/envelope.nrrd
"""
import argparse
import glob
import gzip
import json
import os

import numpy as np
import SimpleITK as sitk
from scipy.ndimage import binary_fill_holes
from skimage import measure

from register_ct_mr import register_cached, load_ct_mr
from preprocess_hn_mri import (output_grid, to_zyx, window_ct_u8, window_mr_u8,
                               mesh_from_mask, OUT_XY, CT_HU_LO, CT_HU_HI)

LABELS = {"1": "body envelope", "2": "tumour envelope", "3": "organ envelope"}
LABEL_COLORS = {"1": [90, 140, 200], "2": [255, 150, 70], "3": [150, 110, 205]}


def body_mask(ct_hu_zyx):
    """Largest connected component of non-air (HU > -500), holes filled = the patient body."""
    m = ct_hu_zyx > -500.0
    lab = measure.label(m)
    if lab.max() == 0:
        return m
    m = lab == (int(np.argmax(np.bincount(lab.flat)[1:])) + 1)
    return binary_fill_holes(m)


def _to_grid(img_path, ref):
    """Read a mask NRRD and resample (nearest) onto the shared output grid by physical space."""
    r = sitk.Resample(sitk.ReadImage(img_path, sitk.sitkUInt8), ref, sitk.Transform(),
                      sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)
    return to_zyx(r) > 0


def main():
    ap = argparse.ArgumentParser(description="Build a body+tumour+organ multi-label dataset.")
    ap.add_argument("--case-dir", required=True)
    ap.add_argument("--tumour", required=True, help="tumour-envelope NRRD on the CT grid (triage output)")
    ap.add_argument("--id", default=None)
    ap.add_argument("--title", default=None)
    a = ap.parse_args()
    case = os.path.basename(os.path.normpath(a.case_dir))
    ds_id = a.id or f"hanseg_{case}_envelopes"
    out_dir = os.path.join("public", "data", ds_id)
    os.makedirs(out_dir, exist_ok=True)

    print(f"[{ds_id}] loading + registering…")
    ct, mr = load_ct_mr(a.case_dir)
    tx = register_cached(ct, mr, case)
    ref, out_size, out_spacing = output_grid(ct)

    # intensity volumes on the shared output grid
    ct_hu = to_zyx(sitk.Resample(ct, ref, sitk.Transform(), sitk.sitkLinear, 0.0, sitk.sitkFloat32))
    ct_u8 = window_ct_u8(ct_hu)
    mr_u8 = window_mr_u8(to_zyx(sitk.Resample(mr, ref, tx, sitk.sitkLinear, 0.0, sitk.sitkFloat32)))

    # three envelope layers
    body = body_mask(ct_hu)
    tumour = _to_grid(a.tumour, ref)
    organ = np.zeros(body.shape, bool)
    for f in sorted(glob.glob(os.path.join(a.case_dir, "*OAR_*.nrrd"))):
        organ |= _to_grid(f, ref)

    # paint most-specific last so it wins shared voxels: body < organ < tumour
    seg = np.zeros(body.shape, np.uint8)
    seg[body] = 1
    seg[organ] = 3
    seg[tumour] = 2

    phys = np.array([out_size[i] * out_spacing[i] for i in range(3)])
    ext = (phys / phys.max()).tolist()

    meshes = []
    # coarser marching-cubes step for the big body shell (context only) than for the
    # small, detail-worthy tumour / organ layers — keeps body.json from ballooning.
    for lab, mask, fname, step in ((1, body, "body.json", 3), (2, tumour, "tumor.json", 1),
                                   (3, organ, "organ.json", 1)):
        mesh, nv, nf = mesh_from_mask(mask, ext, step=step)
        json.dump(mesh, open(os.path.join(out_dir, fname), "w"))
        meshes.append({"label": lab, "file": fname})
        print(f"  label {lab} {LABELS[str(lab)]:16s} {int(mask.sum()):>8} vox  {nv}v/{nf}f")

    for name, arr in (("ct.bin.gz", ct_u8), ("mri.bin.gz", mr_u8), ("seg.bin.gz", seg)):
        with open(os.path.join(out_dir, name), "wb") as fp:
            fp.write(gzip.compress(arr.reshape(-1).tobytes(), 6))

    manifest = {
        "id": ds_id,
        "title": a.title or f"HaN-Seg · {case} · envelopes (body / tumour / organ)",
        "source": "HaN-Seg (Podobnik et al., Zenodo 7442914) — CT + T1 MR + OAR masks",
        "modality": "CT + MR · T1",
        "dims": [OUT_XY, OUT_XY, out_size[2]],
        "worldExtent": ext,
        "spacingMm": [round(s, 4) for s in out_spacing],
        "storageWindowHU": {"lo": CT_HU_LO, "hi": CT_HU_HI},
        "defaultWL": {"window": 0.85, "level": 0.5},
        "mriWL": {"window": 0.9, "level": 0.5},
        "hasSegmentation": True,
        "labels": LABELS,
        "labelColors": LABEL_COLORS,
        "clinicalNote": ("Three toggleable envelopes on one grid: body (CT), the MedSAM2 "
                         "tumour-stand-in recall-safe envelope, and the union of all OARs. "
                         "Demonstrates the triage layering — the 'tumour' is an OAR stand-in, "
                         "not a real lesion."),
        "timepoints": [{
            "id": "t0", "label": "CT + MR T1",
            "ct": "ct.bin.gz", "mri": "mri.bin.gz", "seg": "seg.bin.gz",
            "meshes": meshes,
        }],
        "meshes": None, "metrics": None,
    }
    json.dump(manifest, open(os.path.join(out_dir, "manifest.json"), "w"), indent=2)
    print(f"wrote {out_dir}  dims {[OUT_XY, OUT_XY, out_size[2]]}")
    print("  run `npm run data:index` to add it to the picker")


if __name__ == "__main__":
    main()
