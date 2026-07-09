"""Head & neck case: build a 3D tumour from a SINGLE annotated slice.

Input is the standard radiotherapy export — a DICOM CT series + a DICOM RTSTRUCT
whose ROI is contoured on just one axial slice. CT-only H&N tumour has poor
soft-tissue contrast, so this produces a ROUGH visualization envelope, not a
measurement-grade contour (see caveats in the README).

Pipeline:
  1. rt-utils rasterizes the RTSTRUCT contour onto the CT grid (mask aligned to
     the sorted CT series by construction — no orientation guesswork).
  2. The single annotated slice is propagated up/down the stack into a 3D mask,
     fully automatic (no per-case interaction). Two modes:
       geometric (default) — ellipsoidal taper of the real contour; robust on
                             CT-only H&N, where intensity can't separate the
                             iso-dense tumour from surrounding muscle.
       intensity (opt-in)  — HU region-grow, for contrast-distinct tumours.
  3. Marching cubes -> mesh, emitted in the same normalized world space +
     asset/manifest format as the other datasets.

    .venv/bin/python scripts/preprocess_hn.py \
        --dicom /path/to/CT_dicom_dir --rtstruct /path/to/rtstruct.dcm [--roi GTV]

Then `npm run data:index` to add it to the patient picker.
"""
import argparse, os, json, shutil
import numpy as np
from scipy import ndimage
from skimage.morphology import disk
from skimage.measure import label as cclabel
from rt_utils import RTStructBuilder

from asset_common import mesh_from_mask, window_u8, write_gz  # shared pure asset helpers

OUT_XY = 256          # in-plane output resolution
OUT_Z_CAP = 220       # cap on output slices
HU_LO, HU_HI = -200, 400   # soft-tissue storage window (H&N)


# --------------------------------------------------------------------------- IO
def load_ct_and_seed(dicom_dir, rtstruct_path, roi_name):
    """-> ct_hu (X,Y,Z) float32, seed (X,Y,Z) uint8, zooms (sx,sy,sz) mm, pid."""
    rt = RTStructBuilder.create_from(dicom_series_path=dicom_dir, rt_struct_path=rtstruct_path)
    names = rt.get_roi_names()
    if not names:
        raise SystemExit("RTSTRUCT has no ROIs")
    roi = roi_name or names[0]
    if roi not in names:
        raise SystemExit(f"ROI '{roi}' not found. Available: {names}")
    print(f"  ROIs: {names}  ->  using '{roi}'")

    mask = rt.get_roi_mask_by_name(roi)              # (rows=Y, cols=X, Z) bool
    series = rt.series_data                          # sorted to match mask's Z axis
    d0 = series[0]
    slope = float(getattr(d0, "RescaleSlope", 1))
    intercept = float(getattr(d0, "RescaleIntercept", 0))
    ct_zyx = np.stack([s.pixel_array.astype(np.float32) for s in series], axis=0)  # (Z,Y,X)
    ct = (ct_zyx * slope + intercept).transpose(2, 1, 0)          # (X,Y,Z)
    seed = mask.transpose(1, 0, 2).astype(np.uint8)              # (X,Y,Z)

    zpos = sorted(float(s.ImagePositionPatient[2]) for s in series)
    sz = float(abs(np.median(np.diff(zpos)))) if len(zpos) > 1 else float(getattr(d0, "SliceThickness", 1) or 1)
    sx, sy = float(d0.PixelSpacing[1]), float(d0.PixelSpacing[0])
    pid = str(getattr(d0, "PatientID", "anon"))
    nz = int(seed.any(axis=(0, 1)).sum())
    print(f"  CT {ct.shape}  spacing {sx:.3f}×{sy:.3f}×{sz:.3f} mm  | seed on {nz} slice(s)")
    return ct, seed, (sx, sy, sz), pid


# ------------------------------------------------------------------ propagation
def _seed_slice(seed):
    zsl = np.where(seed.any(axis=(0, 1)))[0]
    if zsl.size == 0:
        raise SystemExit("seed mask is empty")
    k0 = int(round(zsl.mean()))
    return (k0, seed[:, :, k0]) if seed[:, :, k0].any() else (int(zsl[0]), seed[:, :, zsl[0]])


def propagate_geometric(seed, zooms, z_span_mm=None):
    """Default. Model the tumour as ~ellipsoidal: shrink the real annotated
    contour toward zero over a z-extent derived from its in-plane size. Uses only
    the contour shape, so it never leaks or inflates — the right rough-viz choice
    when CT intensity can't separate tumour from iso-dense muscle (typical H&N)."""
    X, Y, Z = seed.shape
    k0, seed2d = _seed_slice(seed)
    edt = ndimage.distance_transform_edt(seed2d)          # in-plane radius map (voxels)
    rmax_px = float(edt.max())
    if rmax_px <= 0:
        raise SystemExit("seed contour is empty / 1px")
    # default half-height = in-plane equivalent radius (roughly spherical tumour)
    zr_mm = z_span_mm if z_span_mm else rmax_px * (zooms[0] + zooms[1]) / 2
    zr = max(1, int(round(zr_mm / zooms[2])))
    out = seed.astype(bool).copy()
    for step in (1, -1):
        for d in range(1, zr + 1):
            k = k0 + step * d
            if k < 0 or k >= Z:
                break
            scale = np.sqrt(max(0.0, 1.0 - (d / zr) ** 2))   # ellipsoidal profile
            if scale <= 0:
                break
            out[:, :, k] = edt > (1.0 - scale) * rmax_px      # erode contour proportionally
    return out.astype(np.uint8)


def propagate_intensity(ct, seed, zooms, hu_pad=60.0, inplane_mm=6.0, z_span_mm=80.0, min_frac=0.08):
    """Opt-in (--mode intensity). Region-grow within the seed's HU band, bounded
    per-slice to a dilation of the previous slice, tapering off below min_frac of
    the seed area. Good for contrast-distinct tumours; on iso-dense tissue it can
    inflate, so geometric is the default."""
    X, Y, Z = ct.shape
    k0, seed2d = _seed_slice(seed)
    seed_area = int(seed2d.sum())
    vals = ct[seed > 0]
    lo, hi = np.percentile(vals, 2) - hu_pad, np.percentile(vals, 98) + hu_pad
    band = (ct >= lo) & (ct <= hi)
    se = disk(max(1, int(round(inplane_mm / ((zooms[0] + zooms[1]) / 2)))))
    kmax = max(1, int(round(z_span_mm / zooms[2])))

    out = seed.astype(bool).copy()
    for step in (1, -1):
        prev = seed2d.astype(bool)
        for d in range(1, kmax + 1):
            k = k0 + step * d
            if k < 0 or k >= Z:
                break
            cand = band[:, :, k] & ndimage.binary_dilation(prev, structure=se)
            lbl = cclabel(cand)
            keep = set(np.unique(lbl[prev & (lbl > 0)])) - {0}
            if not keep:
                break
            new2d = ndimage.binary_fill_holes(np.isin(lbl, list(keep)))
            if new2d.sum() < min_frac * seed_area:
                break
            out[:, :, k] = new2d
            prev = new2d

    lbl3 = cclabel(out)
    if lbl3.max() > 0:
        sizes = np.bincount(lbl3.ravel()); sizes[0] = 0
        out = lbl3 == sizes.argmax()
    return ndimage.binary_closing(out, iterations=1).astype(np.uint8)


# ------------------------------------------------------------- grid + meshing
def resample_idx(shape, oz):
    return (np.round(np.linspace(0, shape[0] - 1, OUT_XY)).astype(int),
            np.round(np.linspace(0, shape[1] - 1, OUT_XY)).astype(int),
            np.round(np.linspace(0, shape[2] - 1, oz)).astype(int))


def to_out(vol, idx):
    xi, yi, zi = idx
    return vol[np.ix_(xi, yi, zi)].transpose(2, 1, 0)   # (Z,Y,X)


# --------------------------------------------------------------------- emit
def build(ct, tumor_native, zooms, out_dir, ds_id, title, roi):
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    oz = int(min(ct.shape[2], OUT_Z_CAP))
    idx = resample_idx(ct.shape, oz)
    ct_u8 = window_u8(to_out(ct, idx), HU_LO, HU_HI)
    tumor_out = (to_out(tumor_native, idx) > 0).astype(np.uint8)        # (Z,Y,X)
    if tumor_out.sum() == 0:
        raise SystemExit("propagated tumour empty after resample — check the seed/params")

    phys = np.array([ct.shape[0] * zooms[0], ct.shape[1] * zooms[1], ct.shape[2] * zooms[2]])
    ext = (phys / phys.max()).tolist()

    seg = np.where(tumor_out > 0, 2, 0).astype(np.uint8)
    tmesh, tv, tf = mesh_from_mask(tumor_out, ext)
    json.dump(tmesh, open(os.path.join(out_dir, "tumor.json"), "w"))

    nz = np.argwhere(tumor_native > 0)
    bb = (nz.max(0) - nz.min(0) + 1)
    bbmm = [round(float(bb[a] * zooms[a]), 1) for a in range(3)]
    vox = int(tumor_native.sum())
    metrics = {"tumorVolumeCm3": round(vox * float(np.prod(zooms)) / 1000.0, 1),
               "maxDiameterMm": max(bbmm), "meanDiameterMm": round(sum(bbmm) / 3, 1),
               "tumorVoxels": vox, "bboxMm": bbmm}
    json.dump(metrics, open(os.path.join(out_dir, "metrics.json"), "w"))

    write_gz(os.path.join(out_dir, "ct.bin.gz"), ct_u8)
    write_gz(os.path.join(out_dir, "seg.bin.gz"), seg)

    note = "Tumour is a rough envelope propagated from a single annotated slice (CT-only) — not measurement-grade"
    manifest = {
        "id": ds_id, "title": title,
        "source": f"Head & neck CT + RTSTRUCT '{roi}' (single-slice contour, auto-propagated)",
        "modality": "CT", "dims": [OUT_XY, OUT_XY, oz], "worldExtent": ext,
        "spacingMm": list(zooms), "storageWindowHU": {"lo": HU_LO, "hi": HU_HI},
        "defaultWL": {"window": 0.6, "level": 0.4},
        "hasSegmentation": True, "labels": {"2": "tumour"}, "clinicalNote": note,
        "timepoints": [{"id": "t0", "label": "single acq", "ct": "ct.bin.gz",
                        "seg": "seg.bin.gz", "tumorMesh": "tumor.json"}],
        "meshes": None, "metrics": "metrics.json",
    }
    json.dump(manifest, open(os.path.join(out_dir, "manifest.json"), "w"), indent=2)
    print(f"  tumour {tv}v/{tf}f  {metrics['tumorVolumeCm3']}cm³  bbox {bbmm}mm")
    print(f"wrote {out_dir} — dims {[OUT_XY, OUT_XY, oz]}, extent {[round(x,3) for x in ext]}")


def main():
    ap = argparse.ArgumentParser(description="Build a H&N tumour dataset from DICOM CT + RTSTRUCT (single-slice contour).")
    ap.add_argument("--dicom", required=True, help="directory of the CT DICOM series")
    ap.add_argument("--rtstruct", required=True, help="RTSTRUCT .dcm file")
    ap.add_argument("--roi", default=None, help="ROI name (default: first ROI)")
    ap.add_argument("--id", default=None, help="dataset id (default: hn_<PatientID>)")
    ap.add_argument("--title", default=None, help="display title")
    ap.add_argument("--mode", choices=["geometric", "intensity"], default="geometric",
                    help="geometric: ellipsoidal taper of the contour (default, robust on iso-dense CT); "
                         "intensity: HU region-grow (for contrast-distinct tumours)")
    ap.add_argument("--z-span-mm", type=float, default=None,
                    help="half-height of the tumour in z (mm). geometric default: in-plane radius; intensity default: 80")
    ap.add_argument("--hu-pad", type=float, default=60.0, help="[intensity] HU band padding around seed intensities")
    ap.add_argument("--inplane-mm", type=float, default=6.0, help="[intensity] per-slice growth bound (mm)")
    a = ap.parse_args()

    ct, seed, zooms, pid = load_ct_and_seed(a.dicom, a.rtstruct, a.roi)
    if a.mode == "geometric":
        tumor = propagate_geometric(seed, zooms, z_span_mm=a.z_span_mm)
    else:
        tumor = propagate_intensity(ct, seed, zooms, hu_pad=a.hu_pad, inplane_mm=a.inplane_mm,
                                    z_span_mm=a.z_span_mm or 80.0)
    span = int(tumor.any(axis=(0, 1)).sum())
    print(f"  propagated across {span} slices (seed was on {int(seed.any(axis=(0,1)).sum())})")
    ds_id = a.id or f"hn_{pid}"
    build(ct, tumor, zooms, os.path.join("public", "data", ds_id), ds_id,
          a.title or f"H&N · {pid}", a.roi or "ROI")


if __name__ == "__main__":
    main()
