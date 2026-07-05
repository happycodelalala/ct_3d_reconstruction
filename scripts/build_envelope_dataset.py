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

from preprocess_hn_mri import (prepare_output_volumes, to_zyx, mesh_from_mask,
                               OUT_XY, CT_HU_LO, CT_HU_HI)

# fixed layer colours (RGB 0..255); the tumour layer's colour comes from --tumour-color
C_BODY, C_BONE, C_ORGAN = [90, 140, 200], [222, 216, 198], [150, 110, 205]


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
    ap.add_argument("--tumour", required=True, help="tumour NRRD on the CT grid (GT mask or segmentation result)")
    ap.add_argument("--tumour-label", default="tumour envelope", help="name for the tumour layer")
    ap.add_argument("--tumour-color", default="255,150,70", help="tumour layer RGB (e.g. 90,200,110 for GT green)")
    ap.add_argument("--bone-hu", type=float, default=200.0, help="CT HU threshold for the bone layer")
    ap.add_argument("--id", default=None)
    ap.add_argument("--title", default=None)
    a = ap.parse_args()
    case = os.path.basename(os.path.normpath(a.case_dir))
    ds_id = a.id or f"hanseg_{case}_envelopes"
    out_dir = os.path.join("public", "data", ds_id)
    os.makedirs(out_dir, exist_ok=True)

    print(f"[{ds_id}] loading + registering…")
    ref, out_size, out_spacing, ct_hu, ct_u8, mr_u8 = prepare_output_volumes(a.case_dir)

    # envelope layers
    body = body_mask(ct_hu)
    bone = (ct_hu > a.bone_hu) & body           # skeleton = dense CT inside the body
    organ = np.zeros(body.shape, bool)
    for f in sorted(glob.glob(os.path.join(a.case_dir, "*OAR_*.nrrd"))):
        organ |= _to_grid(f, ref)
    tumour_color = [int(x) for x in a.tumour_color.split(",")]

    # (label, name, colour, mask, mesh-step). Painted in THIS order so later entries win
    # shared voxels (body < bone < organ < tumour). Coarser step for the big body shell
    # (context) than the small detail-worthy layers, to keep body.json light. 3D meshes are
    # built per-layer from the full masks, so each shell toggles independently in 3D
    # regardless of the 2D precedence.
    layers = [
        (1, "body envelope", C_BODY, body, 3),
        (4, "bone envelope", C_BONE, bone, 2),
        (3, "organ envelope", C_ORGAN, organ, 1),
        (2, a.tumour_label, tumour_color, _to_grid(a.tumour, ref), 1),
    ]
    seg = np.zeros(body.shape, np.uint8)
    for label, _, _, mask, _ in layers:
        seg[mask] = label

    phys = np.array([out_size[i] * out_spacing[i] for i in range(3)])
    ext = (phys / phys.max()).tolist()

    meshes, labels_map, label_colors = [], {}, {}
    for label, name, color, mask, step in layers:
        mesh, nv, nf = mesh_from_mask(mask, ext, step=step)
        fname = f"mesh{label}.json"
        json.dump(mesh, open(os.path.join(out_dir, fname), "w"))
        meshes.append({"label": label, "file": fname})
        labels_map[str(label)] = name
        label_colors[str(label)] = color
        print(f"  label {label} {name:24s} {int(mask.sum()):>8} vox  {nv}v/{nf}f")

    for name, arr in (("ct.bin.gz", ct_u8), ("mri.bin.gz", mr_u8), ("seg.bin.gz", seg)):
        with open(os.path.join(out_dir, name), "wb") as fp:
            fp.write(gzip.compress(arr.reshape(-1).tobytes(), 6))

    manifest = {
        "id": ds_id,
        "title": a.title or f"HaN-Seg · {case} · full envelope",
        "source": "HaN-Seg (Podobnik et al., Zenodo 7442914) — CT + T1 MR + OAR masks",
        "modality": "CT + MR · T1",
        "dims": [OUT_XY, OUT_XY, out_size[2]],
        "worldExtent": ext,
        "spacingMm": [round(s, 4) for s in out_spacing],
        "storageWindowHU": {"lo": CT_HU_LO, "hi": CT_HU_HI},
        "defaultWL": {"window": 0.85, "level": 0.5},
        "mriWL": {"window": 0.9, "level": 0.5},
        "hasSegmentation": True,
        "labels": labels_map,
        "labelColors": label_colors,
        "clinicalNote": ("Full envelope: body (CT), bone (CT), organ (union of all OARs), and a "
                         f"tumour-stand-in layer ({a.tumour_label}). The 'tumour' is an OAR "
                         "stand-in, not a real lesion."),
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
