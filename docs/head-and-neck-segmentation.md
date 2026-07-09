# Head & Neck Tumour: 3D Reconstruction from a Single Annotated CT Slice

How ONCOVOL turns **one** axial tumour contour into a 3D tumour model you can view,
orbit, and reslice — and, just as importantly, what that model *is not*.

> **TL;DR** — A clinician contours the tumour on a single CT slice and exports it as
> a DICOM **RTSTRUCT**. `scripts/preprocess_hn.py` rasterizes that contour onto the CT
> grid, **propagates** it up and down the stack into a 3D mask, meshes it, and writes
> the same asset format every other dataset uses. The default propagation is a
> *geometric* taper of the real contour — a deliberately honest "rough envelope,"
> because a single CT slice does not contain the information needed for a true
> measurement-grade 3D segmentation.

---

## 1. The problem

You have:

- a **CT series** (axial slices) of a head & neck patient, and
- a tumour outline drawn on **exactly one** of those slices.

You want a **3D tumour** for the workstation: a surface mesh sitting in the same space
as the CT, so it can be orbited and intersected with the reslice planes.

This is the **sparse-annotation → dense-segmentation** problem in its hardest form:
one 2D label, full 3D target.

### Why it is genuinely hard on CT

1. **One slice has no supero-inferior information.** Nothing in a single axial slice
   tells you how far the tumour extends up/down the neck or how its cross-section
   changes there. Any 3D shape is, strictly, an *assumption*.
2. **CT contrast for H&N tumour is poor.** Gross tumour volume (GTV) is frequently
   **iso-dense** with adjacent muscle, vessels, and nodes — similar Hounsfield units
   (HU). The clinical standard for H&N GTV is **PET/CT** (or CT+MRI) precisely because
   plain CT cannot reliably separate tumour from normal soft tissue by intensity.

Consequence: an intensity-driven 3D grower either **leaks** into same-HU neighbours or
**inflates** without a natural stopping point. So the *default* method here does **not**
trust CT intensity — it trusts the one thing that is real: the clinician's contour.

---

## 2. Inputs and the contract

| Input | Format | Notes |
|---|---|---|
| CT volume | DICOM series (a directory of `.dcm` slices) | any axial acquisition |
| Annotation | DICOM **RTSTRUCT** (`.dcm`) | one ROI contoured on one slice |

The RTSTRUCT references the CT by `FrameOfReferenceUID`/SOP instances, and stores the
contour as polygons in **patient (mm) coordinates**, not pixels — so it must be
rasterized back onto the CT grid before anything else.

---

## 3. Pipeline overview

```
 DICOM CT series ─┐
                  ├─▶ (1) rt-utils ─▶ CT volume (X,Y,Z) + seed mask on 1 slice
 DICOM RTSTRUCT ──┘
                                          │
                                          ▼
                          (2) propagate single slice ──▶ 3D tumour mask
                              · geometric  (default)
                              · intensity  (opt-in)
                                          │
                                          ▼
                  (3) resample + window + marching cubes + manifest
                                          │
                                          ▼
                   public/data/<id>/  ── loads in the workstation
```

Each stage maps to a function in [`scripts/preprocess_hn.py`](../scripts/preprocess_hn.py):
`load_ct_and_seed` → `propagate_geometric` / `propagate_intensity` → `build`.

---

## 4. Stage 1 — Read the CT and rasterize the contour

We use **[`rt-utils`](https://github.com/qurit/rt-utils)** rather than hand-rolling
contour rasterization. The key benefit is **alignment for free**:

```python
rt   = RTStructBuilder.create_from(dicom_series_path=dicom_dir, rt_struct_path=rtstruct)
mask = rt.get_roi_mask_by_name(roi)   # (rows=Y, cols=X, Z) boolean, on the CT grid
series = rt.series_data               # CT slices, sorted in the SAME Z order as `mask`
```

`rt-utils` sorts the CT series by slice position and returns the mask in that exact
slice order, so we build the CT volume from `series` in the same order — the CT and the
seed mask are aligned voxel-for-voxel **by construction**, with no separate orientation
or sorting logic to get wrong.

We then standardise axes to `(X, Y, Z)` with `Z` as the axial (slice) axis, read voxel
spacing from `PixelSpacing` + slice-position deltas, and convert pixels to HU with
`RescaleSlope`/`RescaleIntercept`. The result:

- `ct` — `(X, Y, Z)` float32 HU,
- `seed` — `(X, Y, Z)` uint8, non-zero on the single annotated slice,
- `zooms` — `(sx, sy, sz)` mm.

---

## 5. Stage 2 — Propagate one slice into a 3D mask

Let `k0` be the annotated slice and `seed2d` its 2D mask.

### 5a. Geometric taper — the default (`--mode geometric`)

**Idea:** model the tumour as roughly ellipsoidal in the cranio-caudal direction and
shrink the *real* contour toward a point as you move away from `k0`. The shape is the
clinician's actual outline; only its *scale* is modelled.

**How the per-slice shrink is done.** We take the Euclidean distance transform (EDT) of
the seed mask — for every interior pixel, its distance to the contour boundary:

```
edt      = distance_transform_edt(seed2d)     # interior "radius map", in voxels
rmax_px  = edt.max()                           # in-plane "radius" of the contour
```

To produce a contour scaled by a factor `s ∈ [0,1]`, we simply threshold the EDT:

```
slice_at_scale(s) = edt > (1 - s) * rmax_px
```

- `s = 1` → threshold 0 → the full contour.
- `s → 0` → threshold → `rmax_px` → empty.
- Intermediate `s` erodes the contour inward by `(1-s)·rmax_px`, i.e. scales its
  effective radius by `s` while preserving its shape.

We sweep slices outward from `k0` with an **ellipsoidal** scale profile:

```
for d = 1, 2, … up to zr (each direction ±):
    s = sqrt( 1 − (d / zr)^2 )         # half-ellipse: 1 at the centre, 0 at d = zr
    mask[:, :, k0 ± d] = edt > (1 − s) * rmax_px
```

**Half-height `zr`.** By default we assume the tumour is roughly as tall as it is wide,
so `zr` (in voxels) comes from the in-plane radius:

```
zr_mm = rmax_px · (sx + sy)/2          # in-plane equivalent radius in mm
zr    = round(zr_mm / sz)              # converted to slices  (override with --z-span-mm)
```

**Why this is the default.** It cannot leak into same-HU tissue and cannot inflate —
it is bounded by the contour and `zr`. It degrades gracefully: worst case you get a
smooth blob centred on the truth slice, which is exactly the "rough visualization"
target. It does **not** depend on CT intensity, which on H&N CT is unreliable.

### 5b. Intensity region-grow — opt-in (`--mode intensity`)

For tumours that *are* clearly contrast-distinct (e.g. enhancing lesions), CT intensity
can do better than a generic ellipsoid. This mode grows the mask slice by slice:

1. **Intensity band** from the seed: `[p2 − pad, p98 + pad]` HU (`pad = --hu-pad`),
   robust to outliers via the 2nd/98th percentiles of the seed voxels.
2. **Per-slice spatial bound:** on slice `k±1`, candidates must lie within a dilation
   of the previous slice's mask (`--inplane-mm`) — this is what stops a runaway grow
   into a connected same-HU structure.
3. **Connectivity:** keep only candidate components touching the previous slice; fill
   holes.
4. **Taper / stop:** stop a direction when the new slice's area falls below `min_frac`
   of the seed area (the tumour ending) or the `--z-span-mm` limit is hit.
5. **Cleanup:** keep the largest 3D connected component, then a binary closing.

**Why it is *not* the default.** When the HU band can't separate tumour from
background — the common H&N-on-CT case — step 1 admits the background too, and the grow
is bounded only by step 2's dilation, so it tends to **inflate a tube** rather than
trace the tumour. Geometric is the safer rough-viz default; reach for intensity only
when you know the lesion stands out on CT.

---

## 6. Stage 3 — Resample, window, mesh, and manifest

This stage is shared with the other datasets so H&N data lands in the **same normalized
world space**, which is what keeps the 2D reslices and the 3D mesh consistent (the
coordinate contract is [docs/schema.md §8](schema.md#8-coordinate--grid-conventions)).

- **Resample** to a fixed grid: `256 × 256 × min(Z, 220)`, picking nearest source
  indices per axis.
- **Window** the CT to 8-bit over a soft-tissue range `HU ∈ [−200, 400]` (good for H&N
  soft tissue; the app re-windows interactively on top of this). Stored gzipped.
- **`worldExtent`** is the physical bounding box `(X·sx, Y·sy, Z·sz)` normalized so the
  largest axis = 1, so anatomy keeps its real aspect ratio.
- **Mesh** the tumour mask with a light Gaussian smooth + marching cubes
  (`mesh_from_mask`); vertices are mapped into `[−ext, +ext]` — the same space the CT
  cut-plane lives in, so the mesh and the slices register.
- **Metrics** (volume, max/mean diameter, bbox) are measured from the *native-resolution*
  propagated mask, in mm³ via the voxel spacing — and flagged as estimates.
- **Manifest** uses the unified schema with `labels: {"2": "tumour"}`, a single
  timepoint, no organ envelope, and a `clinicalNote` stating it is a rough envelope.

Output:

```
public/data/<id>/
  manifest.json   dataset descriptor
  ct.bin.gz       gzipped uint8 windowed CT volume
  seg.bin.gz      gzipped uint8 label volume (2 = tumour)
  tumor.json      tumour isosurface { positions[], indices[] }
  metrics.json    measured (estimated) tumour metrics
```

`npm run data:index` adds it to `public/data/index.json`, and it shows up in the patient
picker like any other case. No app code changes are needed.

---

## 7. Usage

```bash
# one-time Python env (see README "Regenerating the data")
uv venv .venv
uv pip install --python .venv numpy scipy scikit-image pydicom rt-utils

# build a head & neck case
.venv/bin/python scripts/preprocess_hn.py \
    --dicom    /path/to/CT_dicom_dir \
    --rtstruct /path/to/rtstruct.dcm \
    --roi      GTV                     # optional; defaults to the first ROI
npm run data:index                     # register it in the picker
npm run dev                            # view it
```

Useful flags:

| Flag | Meaning | Default |
|---|---|---|
| `--mode` | `geometric` (taper) or `intensity` (HU grow) | `geometric` |
| `--z-span-mm` | tumour half-height in z | geometric: in-plane radius · intensity: 80 |
| `--hu-pad` | *(intensity)* HU band padding around seed | 60 |
| `--inplane-mm` | *(intensity)* per-slice growth bound | 6 |
| `--id`, `--title` | dataset id / display name | derived from `PatientID` |

If the geometric envelope looks too tall/short for a given lesion, set `--z-span-mm`.

---

## 8. Limitations — read this before trusting the model

- **Rough visualization, not measurement-grade.** Suitable for showing *where* and
  *roughly how big* the tumour is in 3D. Do **not** use it for volumetry, radiotherapy
  planning, or response assessment.
- **Assumes the annotated slice is near the largest cross-section.** Propagation grows
  outward from it; if the contour is on a tumour tip, the 3D shape will be wrong.
- **Geometric mode imposes an ellipsoidal z-profile** — it will not capture irregular
  superior/inferior shape changes, because that information is not in a single slice.
- **Intensity mode can leak/inflate** on iso-dense H&N CT (hence not the default).
- **CT-only ceiling.** Fundamentally, plain CT under-determines H&N GTV. PET/CT or MRI
  is required for accurate boundaries.

---

## 9. Validation

The pipeline is covered by an end-to-end test on **synthetic** data
(`scripts` test harness): it generates a CT DICOM series and a single-slice RTSTRUCT
(authored with `rt-utils`), runs the full path, and asserts that:

- the contour is read back on exactly one slice and the CT loads as `(X, Y, Z)`,
- geometric propagation expands one slice into a bounded multi-slice mask that does not
  fill the volume,
- intensity propagation spans multiple slices on a contrast phantom,
- `build` emits a valid manifest, correctly-sized gzipped volumes, a non-degenerate
  mesh, and positive metrics.

It has also been rendered in the live app (loads via the picker, tumour mesh visible on
the cut-plane, reslice tiles consistent, organ toggle correctly disabled).

---

## 10. Extending toward real segmentation

The propagation step is intentionally the **only** tumour-specific part; everything
downstream (resample → window → mesh → manifest) is generic. To upgrade quality, replace
*just* the function that produces the 3D mask:

- **CT + MRI available *(preferred whenever an MR exists)*:** register the MR to the CT
  and segment on the MR's soft-tissue contrast — MRI resolves the CT iso-density that
  makes this single-slice method necessary in the first place. Full method (alignment +
  segmentation): [**CT + MRI Tumour Segmentation & Fusion**](ct-mri-tumour-segmentation.md).
- **Human-in-the-loop:** 3D Slicer + MONAI Label, `nnInteractive`, or MedSAM2 — annotate
  one slice and propagate/refine interactively, export a 3D mask (NIfTI), then feed it
  through the meshing path.
- **Automatic (needs PET/CT):** a pretrained HECKTOR `nnU-Net` produces a full GTV mask
  with no annotation at all.

In every case the integration point is the same: produce a 3D binary tumour mask aligned
to the CT, and the existing `build` step turns it into a workstation-ready dataset.

---

*Implementation: [`scripts/preprocess_hn.py`](../scripts/preprocess_hn.py). Asset/manifest
format and shared coordinate space: [`docs/schema.md`](schema.md).*
