# ONCOVOL — CT 3D Reconstruction Workstation

A browser workstation that shows a **3D reconstruction** of a tumour next to the
**original axial CT slices** (the natural projection), a full **multi-planar
reformat (MPR)** set, and a **longitudinal timeline** — all driven by **real public
CT data**. Multi-layer (scroll the slices), multi-angle (orbit the volume), and
multi-timepoint (scrub the screening years) in one view.

It is a clinical "imaging-workstation" UI: dark theme, phosphor-cyan for active
data, amber for lesions, teal for organs, with synced crosshairs across every view.

> **No learned model / inference.** Every reconstruction is built from a real
> segmentation mask (expert labels, public dataset annotations, or — for lung
> envelopes — a transparent CT threshold). The 3D meshes are marching-cubes
> isosurfaces of those masks; the metrics are measured from the labelled voxels.

---

## Datasets

Three real cases ship in, switchable in the top bar. Each is described by a unified
`manifest.json`, so the same UI renders all of them.

| Switcher | Patient | Organ | Tumour | Timepoints | Source |
|---|---|---|---|---|---|
| **KiTS19 · KIDNEY** | case_00000 | kidney envelope | ✅ expert mask | 1 | KiTS19 (HuggingFace + GitHub) |
| **NLST · LUNG (×3)** | 104221 | lung envelope | — | **3** (1999/2000/2001) | NLST via IDC + TotalSegmentator |
| **NLST · TUMOUR (×2)** | 100012 | lung envelope | ✅ expert mask (2000 screen) | **2** (1999/2000) | NLST via IDC + NLSTseg |

- **KiTS19 · case_00000** — contrast kidney CT with expert kidney/tumour
  segmentation. Tumour 7.8 cm³; the headline "3D reconstruction of cancer" case.
- **NLST · 104221** — low-dose lung screening CT across three annual screens (real
  longitudinal series). NLST ships no tumour masks, so the lung envelope comes from
  IDC's TotalSegmentator organ segmentations, with a per-timepoint lung-volume
  readout (4935 → 4894 → 3731 cm³).
- **NLST · 100012** — the **all-in-one** case: organ envelope **+** tumour **+**
  time series. Lung envelope at both screens; the expert NLSTseg tumour mask
  (adenocarcinoma, Stage IA, 5.8 cm³) is present at the 2000 diagnostic screen.
  NLSTseg only annotates the diagnostic screen, so the 1999 screen shows lung + CT
  (realistic for a screen-detected cancer).

---

## Quick start

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # type-check (tsc) + production bundle into dist/
```

The app loads the first dataset on startup; switch datasets from the top bar. The
preprocessed assets in `public/data/<id>/` are **gitignored** — regenerate them
with the pipelines below (or the app shows a "LOADING / DATASET UNAVAILABLE" state).

**Stack:** Vite + React + TypeScript · Three.js via `@react-three/fiber` + `drei` ·
`zustand` for shared state. Requires Node ≥ 18 (developed on 25). The data pipelines
use Node (KiTS) and Python via **uv** (NLST).

---

## What's on screen

- **Left rail** — render-layer toggles **one per segmentation label** (each with its
  colour swatch — e.g. body / bone / organ / tumour on an envelope dataset), plus synced
  cut-plane, MPR ortho box, multi-layer slice stack, auto-orbit, and CT windowing
  (window width / level). Toggles are data-driven from the dataset's labels. For
  **CT+MRI datasets** a **CT / MR / FUSION** modality switch appears (with a CT↔MR
  blend slider); every 2D/3D view re-renders from the chosen volume.
- **Centre — the reconstruction.** Orbit with the mouse (multi-angle). Each labelled
  layer is a shell in its own colour (the tumour is a solid glow; body/bone/organ are
  translucent), toggled independently. A glowing cyan plane is the **active axial
  slice**; it tracks the CT panel. **MPR ORTHO BOX** draws the three orthogonal planes
  intersecting at the crosshair.
- **Right — the source + reformats.** The original axial CT (scroll to page slices,
  click to move the crosshair), a 2×2 **MPR grid** — **coronal / sagittal / oblique**
  reslices of the *same* volume plus a **MIP** (maximum-intensity projection) — all
  crosshair-linked with an adjustable oblique angle, then **lesion quantification**
  (or acquisition info for non-segmented timepoints).
- **Bottom — the timeline.** For longitudinal datasets, scrub or ▶ play through the
  screening years; the whole workstation updates to that timepoint.

The axial, coronal, sagittal and oblique views are all **resliced from one 3D
volume** — exactly how a radiology workstation derives them, no extra data needed.
Every 2D reformat and every 3D plane is generated from the same `planeBasis()`, so
they stay perfectly consistent.

---

## How it works

A preprocessing step turns a CT volume (+ optional segmentation) into compact,
browser-ready assets that all live in **one normalized coordinate space**, so the
2D slices and the 3D meshes line up. (Verified: KiTS seg ↔ mesh tumour centroids
agree to < 0.1 voxel; NLSTseg < 0.5 voxel.)

Per dataset, the pipeline emits:

```
public/data/<id>/
  manifest.json        dataset descriptor (see schema below)
  ct_t*.bin.gz         gzipped uint8 windowed CT volume, one per timepoint
  seg_t*.bin.gz        gzipped uint8 label volume (1 = organ, 2 = tumour), optional
  tumor_t*.json        tumour isosurface { positions[], indices[] }, optional
  organ_t*.json        organ isosurface, optional
  metrics.json         measured tumour metrics, optional
```

The browser fetches these, inflates the volumes (it auto-detects whether the host
already gunzipped them), and renders. Reslicing/MIP run on the volume directly; the
meshes load straight into Three.js.

### Unified manifest

```jsonc
{
  "id": "nlst_100012",
  "title": "NLST · 100012",
  "modality": "CT · LDCT",
  "dims": [256, 256, 160],              // X, Y, Z(=axial slice axis)
  "worldExtent": [0.892, 0.892, 1.0],   // half-width per axis (shared space)
  "spacingMm": [0.547, 0.547, 2.0],
  "defaultWL": { "window": 0.72, "level": 0.4 },
  "hasSegmentation": true,
  "labels": { "1": "lung", "2": "tumour" },
  "clinicalNote": "Adenocarcinoma · Stage IA · right upper lobe",
  "timepoints": [
    { "id": "t0", "label": "1999-01-02", "ct": "ct_t0.bin.gz", "seg": "seg_t0.bin.gz", "organMesh": "organ_t0.json", "lungVolumeCm3": 6197 },
    { "id": "t1", "label": "2000-01-02", "ct": "ct_t1.bin.gz", "seg": "seg_t1.bin.gz", "organMesh": "organ_t1.json", "tumorMesh": "tumor_t1.json", "lungVolumeCm3": 6098 }
  ],
  "metrics": "metrics.json"
}
```

---

## Regenerating the data

Assets are gitignored (licensing + size). Each pipeline downloads one case and
writes `public/data/<id>/`. Python pipelines use a `uv` environment:

```bash
uv venv .venv
uv pip install --python .venv idc-index pydicom nibabel numpy scipy scikit-image remotezip openpyxl rt-utils
```

### 1. KiTS19 — kidney + tumour (NIfTI, Node)

```bash
mkdir -p rawdata
curl -L "https://huggingface.co/datasets/neheller/KiTS-Challenge-Imaging/resolve/main/images/case_00000.nii.gz" -o rawdata/case_00000_img.nii.gz
curl -L "https://raw.githubusercontent.com/neheller/kits19/master/data/case_00000/segmentation.nii.gz" -o rawdata/case_00000_seg.nii.gz
npm run data:kits          # scripts/preprocess.cjs -> public/data/kits_case00000/
```

`preprocess.cjs` parses the NIfTI (nifti-reader-js), windows + downsamples the CT,
and runs marching cubes (`isosurface`) on the kidney/tumour masks.

### 2. NLST · 104221 — longitudinal lungs (DICOM, Python)

NLST imaging is openly downloadable from **NCI Imaging Data Commons** (no account).
Download the 3 screening CTs + their TotalSegmentator SEGs with `idc-index`, then:

```bash
.venv/bin/python - <<'PY'
from idc_index import IDCClient
c = IDCClient(); df = c.index
ct  = df[(df.PatientID=="104221") & (df.Modality=="CT")  & (df.instanceCount>50)]
seg = df[(df.PatientID=="104221") & (df.Modality=="SEG")]
c.download_from_selection(seriesInstanceUID=ct.SeriesInstanceUID.tolist(),  downloadDir="nlst_data",
                          dirTemplate="%PatientID/%StudyInstanceUID/%SeriesInstanceUID")
ct[["StudyDate","StudyInstanceUID","SeriesInstanceUID","SeriesDescription"]].to_csv("nlst_data/_manifest.csv", index=False)
c.download_from_selection(seriesInstanceUID=seg.SeriesInstanceUID.tolist(), downloadDir="nlst_seg",
                          dirTemplate="%SeriesInstanceUID")
PY
.venv/bin/python scripts/preprocess_nlst.py    # -> public/data/nlst_104221/
```

`preprocess_nlst.py` windows + resamples each CT, reads the matching TotalSegmentator
DICOM SEG (matched via its referenced CT series), extracts the lung lobes, and writes
a per-timepoint lung label volume + marching-cubes lung mesh (scikit-image).

### 3. NLST · 100012 — lungs + tumour + time (NIfTI + DICOM, Python)

The expert tumour mask comes from **NLSTseg** (Zenodo `10.5281/zenodo.14838349`,
34 GB of zips) — but a single patient's CT + tumour NIfTI can be pulled over HTTP
with `remotezip` (no full-zip download). The 1999 screen's CT comes from IDC.

```bash
# tumour CT + mask (2000 screen) from NLSTseg, just this patient's two files
.venv/bin/python - <<'PY'
from remotezip import RemoteZip
import os; os.makedirs("nlstseg_data", exist_ok=True)
url = "https://zenodo.org/records/14838349/files/2_LungTumor.zip?download=1"
with RemoteZip(url) as z:
    for k in ("CT","tumor"):
        z.extract(f"NLSTseg_2_LungTumor/100012/100012_{k}.nii.gz", path="nlstseg_data")
PY
# prior screen (1999) CT from IDC
.venv/bin/python - <<'PY'
from idc_index import IDCClient
IDCClient().download_from_selection(
    seriesInstanceUID=["1.2.840.113654.2.55.335938848402215862539398120263494500079"],
    downloadDir="nlst_tp", dirTemplate="%SeriesInstanceUID")
PY
.venv/bin/python scripts/preprocess_nlst_tumor.py   # -> public/data/nlst_100012/
```

`preprocess_nlst_tumor.py` builds both timepoints into the shared grid (orientation
between the NLSTseg-NIfTI and IDC-DICOM screens is verified consistent), threshold-
segments the lung envelope on each, and meshes the expert tumour at the 2000 screen.

### 4. Head & neck — 3D tumour from a single annotated slice (DICOM + RTSTRUCT)

For a clinical RT export (a CT DICOM series + a DICOM **RTSTRUCT** whose tumour ROI
is contoured on just **one** axial slice), `preprocess_hn.py` rasterizes the contour
(via `rt-utils`, aligned to the CT series) and propagates that single slice into a
full 3D tumour mask, then meshes it like any other dataset. **Full method:**
[docs/head-and-neck-segmentation.md](docs/head-and-neck-segmentation.md).

```bash
.venv/bin/python scripts/preprocess_hn.py \
    --dicom /path/to/CT_dicom_dir --rtstruct /path/to/rtstruct.dcm --roi GTV
npm run data:index          # add it to the patient picker
```

Two propagation modes:

- **`--mode geometric`** *(default)* — models the tumour as roughly ellipsoidal and
  tapers the real contour toward zero over a z-extent derived from its in-plane size
  (override with `--z-span-mm`). Uses only the contour shape, so it never leaks or
  inflates — the right choice for CT-only H&N, where the tumour is often iso-dense
  with surrounding muscle.
- **`--mode intensity`** — HU region-grow bounded per slice; better for clearly
  contrast-distinct tumours, but can inflate on iso-dense tissue.

> **Rough visualization only.** A single CT slice carries no real information about
> how the tumour changes shape above/below it, so this is a plausible envelope for
> the 3D view, **not** a measurement-grade contour. It assumes the annotated slice is
> near the tumour's largest cross-section. For accurate H&N GTV you need PET/CT or
> human-in-the-loop tools (3D Slicer + MONAI Label / nnInteractive / MedSAM2); the
> script's propagation step is deliberately isolated so a learned mask can drop into
> the same meshing/manifest path later.

### 5. HaN-Seg — CT + MRI fusion (NRRD, Python / SimpleITK)

Paired head & neck **CT + T1 MR** (+ organ-at-risk masks), openly downloadable from
Zenodo (no account). This exercises the **CT+MRI** path: register the MR onto the CT,
carry *both* volumes in one grid, and fuse them in the workstation. **Full method (how
to align CT↔MRI and segment the tumour with both):**
[docs/ct-mri-tumour-segmentation.md](docs/ct-mri-tumour-segmentation.md).

```bash
mkdir -p hanseg_data
curl -L "https://zenodo.org/records/7442914/files/HaN-Seg.zip?download=1" -o hanseg_data/HaN-Seg.zip
.venv/bin/python -c "import zipfile; zipfile.ZipFile('hanseg_data/HaN-Seg.zip').extractall('hanseg_data')"
uv pip install --python .venv SimpleITK scikit-image

# (optional) sanity-check the MR->CT registration on one case (writes QA overlays)
.venv/bin/python scripts/register_ct_mr.py --case-dir hanseg_data/HaN-Seg/set_1/case_01

# build a CT+MR dataset — the mandible OAR stands in for the tumour
.venv/bin/python scripts/preprocess_hn_mri.py --case-dir hanseg_data/HaN-Seg/set_1/case_01
npm run data:index
```

`register_ct_mr.py` aligns MR→CT with Mattes mutual information (a coarse
cranio-caudal seed → rigid+affine, line-search optimiser + never-regress guard —
robust to HaN-Seg's differing CT/MR frames and FOV). `preprocess_hn_mri.py`
resamples the CT, the registered MR, and a chosen OAR (default: **mandible, as a
tumour stand-in** — HaN-Seg ships no GTV) into one shared grid, and emits the unified
assets with a second `mri` volume per timepoint. Swap the OAR for a real tumour mask
later; nothing downstream changes.

**MedSAM2 zero-shot segmentation (optional).** Instead of the ground-truth OAR, you can
produce the mask with MedSAM2 — a single seed slice propagated to a 3D mask — and feed *that*
into `preprocess_hn_mri.py`. On `case_01` the mechanism reaches **Dice ≈0.89** (CT mask-seed,
tight ROI crop). Full install + usage runbook: **[docs/medsam2-setup.md](docs/medsam2-setup.md)**.

```bash
# after the MedSAM2 setup in that doc (clone + checkpoint + venv):
.venv/bin/python scripts/medsam2_seed_test.py \
  --case-dir hanseg_data/HaN-Seg/set_1/case_01 --modality ct --prompt mask --crop-margin-mm 6 --surface

# view the prediction in the workstation (drop-in as the tumour mask)
PRED=$(readlink -f runs/medsam2_seed/case_01_ct_mask/pred_mask.nrrd)
.venv/bin/python scripts/preprocess_hn_mri.py --case-dir hanseg_data/HaN-Seg/set_1/case_01 \
  --id hanseg_case_01_medsam2 --roi-label "MedSAM2 mandible" --roi-glob "$PRED"
npm run data:index
```

**Full envelope + GT-vs-segmentation comparison.** `build_envelope_dataset.py` assembles a
**multi-label envelope** — body (CT mask), bone (CT threshold), organ (all-OAR union), and a
tumour layer — into one dataset the workstation renders as **independently-toggleable layers**
(2D overlays + 3D shells, one colour each). Point the tumour layer at the ground-truth OAR *and*
at the MedSAM2 result to get two comparable cases. The triage design + the **recall-safe
envelope** behind the tumour layer: **[docs/tumour-triage-pipeline.md](docs/tumour-triage-pipeline.md)**.

```bash
# recall-safe tumour envelope (triage pipeline)  ->  runs/triage/.../envelope.nrrd
.venv/bin/python scripts/triage_pipeline.py --case-dir hanseg_data/HaN-Seg/set_1/case_01 \
  --oar Brainstem --modality mr --dilate-mm 2
# two full-envelope cases: ground truth (green) vs segmentation (amber)
BS=hanseg_data/HaN-Seg/set_1/case_01/case_01_OAR_Brainstem.seg.nrrd
.venv/bin/python scripts/build_envelope_dataset.py --case-dir hanseg_data/HaN-Seg/set_1/case_01 \
  --tumour "$BS" --tumour-label "brainstem (ground truth)" --tumour-color 90,200,110 --id hanseg_case_01_gt
.venv/bin/python scripts/build_envelope_dataset.py --case-dir hanseg_data/HaN-Seg/set_1/case_01 \
  --tumour runs/triage/case_01_brainstem_mr/envelope.nrrd --tumour-label "brainstem (segmentation)" \
  --tumour-color 255,150,70 --id hanseg_case_01_seg
npm run data:index
```

> **Licensing:** HaN-Seg is CC-BY-NC-ND — keep raw and derived assets local (both are
> gitignored). Do not redistribute processed assets.

---

## Project layout

```
src/
  lib/
    dataset.ts        dataset registry + loader (N timepoints, optional seg/meshes);
                      renders real axial slices; normalized coordinate mapping
    mpr.ts            world-space sampler + reslice (coronal/sagittal/oblique) + MIP
    sliceTexture.ts   wraps a slice/reformat as a Three.js texture
  components/
    App.tsx           workstation layout + dataset switcher
    ControlRail.tsx   render-layer toggles + CT windowing
    Viewer3D.tsx      r3f scene: meshes, synced cut-plane, layer stack, MPR ortho box
    CTPanel.tsx       2D axial CT viewer + crosshair + slice scrubber
    MPRStrip.tsx      coronal / sagittal / oblique / MIP reformat tiles
    StatsPanel.tsx    lesion metrics (segmented) or acquisition info
    Timeline.tsx      timepoint scrubber + playback
  store.ts            zustand state (dataset, timepoint, slice, crosshair, toggles, W/L)
scripts/
  preprocess.cjs          KiTS:  NIfTI -> assets (Node: nifti-reader-js + isosurface)
  preprocess_nlst.py      NLST:  DICOM -> assets (Python: pydicom + scikit-image)
  preprocess_nlst_tumor.py NLSTseg: NIfTI+DICOM -> assets (Python: nibabel + pydicom + skimage)
  preprocess_hn.py        H&N:   DICOM+RTSTRUCT -> assets (Python: rt-utils); a single-slice
                          contour auto-propagated to a rough 3D tumour envelope
  register_ct_mr.py       HaN-Seg: validate MR->CT registration (SimpleITK MI, rigid+affine)
  preprocess_hn_mri.py    HaN-Seg: CT+MR -> assets carrying BOTH volumes (fusion) + OAR-as-tumour mesh
```

To add a dataset: write a preprocessing script that emits the unified `manifest.json`
+ gzipped `uint8` CT volume(s) (+ optional seg/meshes/metrics), then add an entry to
`DATASETS` in `src/lib/dataset.ts`. No component changes needed.

---

## Caveats & honest notes

- **Not a model.** There is no inference in the loop. Tumour masks are ground-truth
  (KiTS, NLSTseg). Lung envelopes are either TotalSegmentator (104221) or a CT
  threshold (100012, clearly an algorithmic envelope — its volume reads a little high
  because the threshold also catches the trachea).
- **No longitudinal tumour ground truth exists publicly** for NLST — NLSTseg
  annotates only the one diagnostic screen per patient, so the 100012 tumour mesh is
  present at the 2000 screen and absent at 1999.
- **Low-dose ≠ crisp reformats.** NLST is ~2.5 mm slices (vs KiTS 0.5 mm), so its
  coronal/sagittal/oblique reslices look coarser along the body axis — expected.
- **GPU:** rendering is WebGL (any GPU). If a learned segmentation model is added
  later, target **ROCm** rather than CUDA on AMD hardware.

## Data sources & licenses

- **KiTS19** — Heller et al., *The KiTS19 Challenge Data*. Imaging via the
  `neheller/KiTS-Challenge-Imaging` HuggingFace dataset; segmentation via
  `github.com/neheller/kits19`. CC BY-NC-SA.
- **NLST** — National Lung Screening Trial, imaging via **NCI Imaging Data Commons**
  (`portal.imaging.datacommons.cancer.gov`), DICOM, CC-BY. TotalSegmentator organ
  segmentations are IDC analysis results.
- **NLSTseg** — pixel-level lung-cancer masks on NLST LDCT (Zenodo
  `10.5281/zenodo.14838349`, *Scientific Data* 2025), CC-BY 4.0.

Raw downloads and derived assets (`rawdata/`, `nlst_data/`, `nlst_seg/`,
`nlstseg_data/`, `nlst_tp/`, `public/data/`, `.venv/`) are gitignored.
