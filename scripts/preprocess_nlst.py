"""Preprocess the downloaded NLST patient (DICOM, multi-timepoint) into the same
compact browser assets as the KiTS pipeline.

CT comes from the screening series; an *organ envelope* (lungs) is extracted from
the matching TotalSegmentator DICOM SEG (downloaded from IDC). NLST ships no
tumour masks, so this emits CT + lung segmentation/mesh only.

    .venv/bin/python scripts/preprocess_nlst.py

Outputs public/data/nlst_104221/:
    ct_t{i}.bin.gz     uint8 windowed CT volume per timepoint
    seg_t{i}.bin.gz    uint8 label volume (1 = lung) per timepoint
    organ_t{i}.json    lung isosurface (marching cubes) per timepoint
    manifest.json      unified dataset manifest
"""
import os, csv, glob, gzip, json
import numpy as np
import pydicom
from skimage import measure
from scipy.ndimage import gaussian_filter

PATIENT = "104221"
SRC = os.path.join("nlst_data", PATIENT)
SEG_SRC = "nlst_seg"
OUT = os.path.join("public", "data", f"nlst_{PATIENT}")
OUT_XY, OUT_Z = 256, 128
HU_LO, HU_HI = -1100, 300

os.makedirs(OUT, exist_ok=True)

dates = {}
with open(os.path.join("nlst_data", "_manifest.csv")) as f:
    for r in csv.DictReader(f):
        dates[r["StudyInstanceUID"]] = r["StudyDate"]

# map source-CT SeriesInstanceUID -> SEG file (via the SEG's ReferencedSeriesSequence)
seg_map = {}
for sp in glob.glob(os.path.join(SEG_SRC, "*", "*.dcm")):
    h = pydicom.dcmread(sp, stop_before_pixels=True)
    ref = h.ReferencedSeriesSequence[0].SeriesInstanceUID
    seg_map[ref] = sp


def mesh_from_mask(mask, ext):
    """mask: (Z,Y,X) 0/1 -> world-space marching-cubes surface (output X,Y,Z order)."""
    sm = gaussian_filter(mask.astype(np.float32), sigma=1.0)
    verts, faces, _, _ = measure.marching_cubes(sm, level=0.5, step_size=2)
    Z, Y, X = mask.shape
    vz, vy, vx = verts[:, 0], verts[:, 1], verts[:, 2]
    wx = ((vx / (X - 1)) - 0.5) * 2 * ext[0]
    wy = ((vy / (Y - 1)) - 0.5) * 2 * ext[1]
    wz = ((vz / (Z - 1)) - 0.5) * 2 * ext[2]
    pos = np.stack([wx, wy, wz], axis=1).round(4).reshape(-1).tolist()
    idx = faces.astype(np.int32).reshape(-1).tolist()
    return {"positions": pos, "indices": idx}, len(verts), len(faces)


studies = [d for d in os.listdir(SRC) if os.path.isdir(os.path.join(SRC, d))]
studies.sort(key=lambda s: dates.get(s, s))

timepoints = []
world_extent = None
spacing_repr = None

for ti, study in enumerate(studies):
    files = glob.glob(os.path.join(SRC, study, "*", "*.dcm"))
    slices = [pydicom.dcmread(fp) for fp in files]
    slices = [s for s in slices if hasattr(s, "ImagePositionPatient")]
    slices.sort(key=lambda s: float(s.ImagePositionPatient[2]))
    d0 = slices[0]
    nz, ny, nx = len(slices), int(d0.Rows), int(d0.Columns)
    sp = [float(d0.PixelSpacing[1]), float(d0.PixelSpacing[0])]
    zs = np.array([float(s.ImagePositionPatient[2]) for s in slices])
    zstep = float(abs(np.median(np.diff(zs))))
    slope, inter = float(getattr(d0, "RescaleSlope", 1)), float(getattr(d0, "RescaleIntercept", 0))

    vol = np.stack([s.pixel_array.astype(np.float32) for s in slices]) * slope + inter

    zi = np.round(np.linspace(0, nz - 1, OUT_Z)).astype(int)
    yi = np.round(np.linspace(0, ny - 1, OUT_XY)).astype(int)
    xi = np.round(np.linspace(0, nx - 1, OUT_XY)).astype(int)

    u8 = np.clip((vol[np.ix_(zi, yi, xi)] - HU_LO) / (HU_HI - HU_LO) * 255.0, 0, 255).astype(np.uint8)
    with open(os.path.join(OUT, f"ct_t{ti}.bin.gz"), "wb") as fh:
        fh.write(gzip.compress(u8.reshape(-1).tobytes(), 6))

    if world_extent is None:
        phys = np.array([nx * sp[0], ny * sp[1], nz * zstep])
        world_extent = (phys / phys.max()).tolist()
        spacing_repr = [sp[0], sp[1], zstep]

    tp = {"id": f"t{ti}", "label": dates.get(study, study), "ct": f"ct_t{ti}.bin.gz"}

    # --- lung organ envelope from the matching TotalSegmentator SEG ---
    seg_path = seg_map.get(d0.SeriesInstanceUID)
    if seg_path:
        seg_ds = pydicom.dcmread(seg_path)
        seg_labels = {int(s.SegmentNumber): s.SegmentLabel for s in seg_ds.SegmentSequence}
        lung_nums = {n for n, l in seg_labels.items() if "lung" in l.lower()}
        arr = seg_ds.pixel_array  # (frames, ny, nx) 0/1
        lung_native = np.zeros((nz, ny, nx), np.uint8)
        for fi, fr in enumerate(seg_ds.PerFrameFunctionalGroupsSequence):
            if int(fr.SegmentIdentificationSequence[0].ReferencedSegmentNumber) not in lung_nums:
                continue
            z = float(fr.PlanePositionSequence[0].ImagePositionPatient[2])
            k = int(np.argmin(np.abs(zs - z)))
            np.maximum(lung_native[k], arr[fi], out=lung_native[k])

        lung_out = lung_native[np.ix_(zi, yi, xi)].astype(np.uint8)  # (Z,Y,X) 0/1
        with open(os.path.join(OUT, f"seg_t{ti}.bin.gz"), "wb") as fh:
            fh.write(gzip.compress(lung_out.reshape(-1).tobytes(), 6))

        mesh, nv, nf = mesh_from_mask(lung_out, world_extent)
        with open(os.path.join(OUT, f"organ_t{ti}.json"), "w") as fh:
            json.dump(mesh, fh)

        lung_cm3 = float(lung_native.sum()) * (sp[0] * sp[1] * zstep) / 1000.0
        tp.update({"seg": f"seg_t{ti}.bin.gz", "organMesh": f"organ_t{ti}.json",
                   "lungVolumeCm3": round(lung_cm3, 0)})
        print(f"  t{ti} {tp['label']}: CT {nx}x{ny}x{nz} | lungs {nv} verts/{nf} tris | lung vol {lung_cm3:.0f} cm³")
    else:
        print(f"  t{ti} {tp['label']}: CT only (no SEG matched)")

    timepoints.append(tp)

manifest = {
    "id": f"nlst_{PATIENT}",
    "title": f"NLST · {PATIENT}",
    "source": "NLST via NCI Imaging Data Commons (idc-index); CT + TotalSegmentator lung SEG",
    "modality": "CT · LDCT",
    "dims": [OUT_XY, OUT_XY, OUT_Z],
    "worldExtent": world_extent,
    "spacingMm": spacing_repr,
    "storageWindowHU": {"lo": HU_LO, "hi": HU_HI},
    "defaultWL": {"window": 0.78, "level": 0.42},
    "hasSegmentation": True,
    "labels": {"1": "lung"},
    "timepoints": timepoints,
    "meshes": None,
    "metrics": None,
}
with open(os.path.join(OUT, "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2)
print(f"\nwrote {OUT}/manifest.json — {len(timepoints)} timepoints, extent {[round(x,3) for x in world_extent]}")
