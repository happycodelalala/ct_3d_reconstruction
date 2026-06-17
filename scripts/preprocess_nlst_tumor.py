"""Build the combined NLST case for patient 100012: organ envelope + tumour +
longitudinal time series, all in one dataset.

Timepoints (same patient, two annual screens; orientation verified consistent):
  t0 1999  — IDC DICOM CT (B30f) + threshold lung envelope  (no tumour annotated)
  t1 2000  — NLSTseg NIfTI CT + expert tumour mask + threshold lung envelope

The expert tumour mask (NLSTseg) exists only at the 2000 diagnostic screen; the
prior screen shows lung + CT. Everything is resampled to one normalized grid.

    .venv/bin/python scripts/preprocess_nlst_tumor.py   ->  public/data/nlst_100012/
"""
import os, gzip, json, glob, shutil
import numpy as np
import nibabel as nib
import pydicom
from skimage import measure
from scipy import ndimage
from scipy.ndimage import gaussian_filter

PID = "100012"
NII = f"nlstseg_data/NLSTseg_2_LungTumor/{PID}"
DICOM_1999 = "nlst_tp/*500079*"   # 1999 B30f series
OUT = os.path.join("public", "data", f"nlst_{PID}")
OUT_XY, OUT_Z = 256, 160
HU_LO, HU_HI = -1100, 300
CLINICAL = "Adenocarcinoma (NOS) · Stage IA · right upper lobe"

if os.path.isdir(OUT):
    shutil.rmtree(OUT)
os.makedirs(OUT)


def segment_lungs(hu):  # (X,Y,Z) HU
    air = hu < -320
    lbl, n = ndimage.label(air)
    faces = np.concatenate([lbl[0].ravel(), lbl[-1].ravel(), lbl[:, 0].ravel(), lbl[:, -1].ravel()])
    mask = air & ~np.isin(lbl, list(set(np.unique(faces)) - {0}))
    lbl2, n2 = ndimage.label(mask)
    if n2 == 0:
        return mask.astype(np.uint8)
    sizes = ndimage.sum(np.ones_like(lbl2), lbl2, range(1, n2 + 1))
    lungs = np.isin(lbl2, np.argsort(sizes)[::-1][:2] + 1)
    lungs = ndimage.binary_fill_holes(ndimage.binary_closing(lungs, iterations=3))
    return lungs.astype(np.uint8)


def load_dicom_xyz(pattern):
    sl = [pydicom.dcmread(f) for f in glob.glob(os.path.join(pattern, "*.dcm"))]
    sl.sort(key=lambda s: float(s.ImagePositionPatient[2]))  # z ascending
    d0 = sl[0]
    vol = np.stack([s.pixel_array.astype(np.float32) for s in sl], 0)  # (Z,Y,X)
    vol = vol * float(getattr(d0, "RescaleSlope", 1)) + float(getattr(d0, "RescaleIntercept", 0))
    zstep = float(abs(np.median(np.diff([float(s.ImagePositionPatient[2]) for s in sl]))))
    zooms = (float(d0.PixelSpacing[1]), float(d0.PixelSpacing[0]), zstep)
    return vol.transpose(2, 1, 0), zooms  # (X,Y,Z)


def mesh_from_mask(mask, ext, sigma, step):
    sm = gaussian_filter(mask.astype(np.float32), sigma=sigma)
    v, fc, _, _ = measure.marching_cubes(sm, level=0.5, step_size=step)
    Zd, Yd, Xd = mask.shape
    ox = ((v[:, 2] / (Xd - 1)) - 0.5) * 2 * ext[0]
    oy = ((v[:, 1] / (Yd - 1)) - 0.5) * 2 * ext[1]
    oz = ((v[:, 0] / (Zd - 1)) - 0.5) * 2 * ext[2]
    return {"positions": np.stack([ox, oy, oz], 1).round(4).reshape(-1).tolist(),
            "indices": fc.astype(np.int32).reshape(-1).tolist()}, len(v), len(fc)


# --- 2000 (NLSTseg NIfTI): CT + tumour ---
ct2000 = nib.load(f"{NII}/{PID}_CT.nii.gz").get_fdata().astype(np.float32)        # (X,Y,Z)
tu2000 = (np.asarray(nib.load(f"{NII}/{PID}_tumor.nii.gz").dataobj) > 0).astype(np.uint8)
z2000 = [float(z) for z in nib.load(f"{NII}/{PID}_CT.nii.gz").header.get_zooms()[:3]]

# --- 1999 (IDC DICOM): CT only ---
ct1999, z1999 = load_dicom_xyz(DICOM_1999)

# shared normalized extent from the 2000 scan
phys = np.array([ct2000.shape[0] * z2000[0], ct2000.shape[1] * z2000[1], ct2000.shape[2] * z2000[2]])
world_extent = (phys / phys.max()).tolist()


def resample_idx(shape):
    return (np.round(np.linspace(0, shape[0] - 1, OUT_XY)).astype(int),
            np.round(np.linspace(0, shape[1] - 1, OUT_XY)).astype(int),
            np.round(np.linspace(0, shape[2] - 1, OUT_Z)).astype(int))


def to_out(vol, idx):
    xi, yi, zi = idx
    return vol[np.ix_(xi, yi, zi)].transpose(2, 1, 0)  # (Z,Y,X)


def process_tp(ti, label, ct, zooms, tumor=None):
    idx = resample_idx(ct.shape)
    ct_u8 = np.clip((to_out(ct, idx) - HU_LO) / (HU_HI - HU_LO) * 255.0, 0, 255).astype(np.uint8)
    lung = segment_lungs(ct)
    lung_out = to_out(lung, idx).astype(np.uint8)
    tp = {"id": f"t{ti}", "label": label, "ct": f"ct_t{ti}.bin.gz", "seg": f"seg_t{ti}.bin.gz",
          "organMesh": f"organ_t{ti}.json",
          "lungVolumeCm3": round(float(lung.sum()) * np.prod(zooms) / 1000.0)}
    seg = lung_out.copy()
    if tumor is not None:
        tu_out = to_out(tumor, idx).astype(np.uint8)
        seg = np.where(tu_out > 0, 2, lung_out).astype(np.uint8)
        tmesh, tv, tf = mesh_from_mask(tu_out, world_extent, 0.6, 1)
        json.dump(tmesh, open(os.path.join(OUT, f"tumor_t{ti}.json"), "w"))
        tp["tumorMesh"] = f"tumor_t{ti}.json"
        # tumour metrics (native)
        nzc = np.argwhere(tumor > 0); bb = (nzc.max(0) - nzc.min(0) + 1)
        bbmm = [round(float(bb[a] * zooms[a]), 1) for a in range(3)]
        global METRICS
        METRICS = {"tumorVolumeCm3": round(int(tumor.sum()) * float(np.prod(zooms)) / 1000.0, 1),
                   "maxDiameterMm": max(bbmm), "meanDiameterMm": round(sum(bbmm) / 3, 1),
                   "tumorVoxels": int(tumor.sum()), "bboxMm": bbmm,
                   "organVolumeCm3": tp["lungVolumeCm3"], "organLabel": "lung"}
        print(f"  t{ti} {label}: tumour {tv}v/{tf}f {METRICS['tumorVolumeCm3']}cm³ | lung {tp['lungVolumeCm3']}cm³")
    else:
        print(f"  t{ti} {label}: lung {tp['lungVolumeCm3']}cm³ (no tumour at this screen)")
    omesh, ov, of = mesh_from_mask(lung_out, world_extent, 1.0, 2)
    json.dump(omesh, open(os.path.join(OUT, f"organ_t{ti}.json"), "w"))
    with open(os.path.join(OUT, f"ct_t{ti}.bin.gz"), "wb") as f:
        f.write(gzip.compress(ct_u8.reshape(-1).tobytes(), 6))
    with open(os.path.join(OUT, f"seg_t{ti}.bin.gz"), "wb") as f:
        f.write(gzip.compress(seg.reshape(-1).tobytes(), 6))
    return tp


METRICS = {}
timepoints = [
    process_tp(0, "1999-01-02", ct1999, z1999, tumor=None),
    process_tp(1, "2000-01-02", ct2000, z2000, tumor=tu2000),
]
json.dump(METRICS, open(os.path.join(OUT, "metrics.json"), "w"))

manifest = {
    "id": f"nlst_{PID}", "title": f"NLST · {PID}",
    "source": f"NLST CT via IDC (1999) + NLSTseg expert tumour (2000, Zenodo 14838349) · {CLINICAL}",
    "modality": "CT · LDCT",
    "dims": [OUT_XY, OUT_XY, OUT_Z], "worldExtent": world_extent,
    "spacingMm": z2000, "storageWindowHU": {"lo": HU_LO, "hi": HU_HI},
    "defaultWL": {"window": 0.72, "level": 0.4},
    "hasSegmentation": True, "labels": {"1": "lung", "2": "tumour"},
    "clinicalNote": CLINICAL,
    "timepoints": timepoints, "meshes": None, "metrics": "metrics.json",
}
json.dump(manifest, open(os.path.join(OUT, "manifest.json"), "w"), indent=2)
print(f"wrote {OUT} — {len(timepoints)} timepoints, extent {[round(x,3) for x in world_extent]}")
