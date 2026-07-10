# ONCOVOL — Data Schema & Contracts

**Status:** canonical source of truth for every on-disk contract between the pipeline and the workstation.
**Companion:** [design.md](design.md) explains architecture and module responsibilities. This file is the reference: exact fields, types, producers, consumers.

Because validation is **partial** (`dataset.ts::validateManifest` guards only the load-bearing fields — see §8/§10) and there are **no shared types across the boundary**, this document *is* the integration contract. When in doubt, this file wins; code that disagrees is a bug or a drift item ([design.md §8](design.md#8-known-drift--alignment-worklist)).

Legend: **P** = produced/written by pipeline · **C** = consumed/read by frontend · `optional?` marks optional keys.

---

## 1. On-disk dataset layout

```
public/data/
  index.json                     registry of all datasets (built by build_index.cjs)
  <id>/
    manifest.json                per-dataset descriptor (contract below)
    ct.bin.gz                    gzipped uint8 windowed CT volume        (required)
    mri.bin.gz                   gzipped uint8 windowed MR volume         (optional; CT+MR datasets)
    seg.bin.gz                   gzipped uint8 multi-label mask           (optional)
    mesh<label>.json             one isosurface per seg label, e.g. mesh1..mesh4  (envelope builder)
    tumor.json / organ.json      legacy single-mesh slots                 (older builders)
    metrics.json                 measured tumour/organ metrics            (optional)
```

Actual example (`public/data/hanseg_case_01_gt/`): `ct.bin.gz`, `mri.bin.gz`, `seg.bin.gz`, `mesh1.json`, `mesh2.json`, `mesh3.json`, `mesh4.json`, `manifest.json`.

> All `*.bin.gz`, `*.nrrd`, `*.nii.gz`, etc. are gitignored — imaging data (HaN-Seg is CC-BY-NC-ND) stays local. Only the schema is version-controlled.

---

## 2. `manifest.json`

One per dataset. The frontend type is `Manifest` in `src/lib/dataset.ts:5-32`.

| Field | Type | P | C | Notes |
|---|---|:-:|:-:|---|
| `id` | string | ✓ | ✓ | Dataset id; matches directory name. |
| `title` | string | ✓ | ✓ | Display title. |
| `source` | string | ✓ | ✓ | Provenance/citation string. |
| `modality` | string | ✓ | ✓ | e.g. `"CT + MR · T1"`. Display only. |
| `dims` | `[number,number,number]` | ✓ | ✓ | `[X, Y, Z]`; **Z is the slice axis**. |
| `worldExtent` | `[number,number,number]` | ✓ | ✓ | Per-axis half-width, normalized so the largest physical axis = 1.0. |
| `spacingMm` | `[number,number,number]` | ✓ | ✓ | Output voxel spacing (mm), `[x,y,z]`. Display + reference only. |
| `storageWindowHU` | `{lo,hi}` optional? | ✓ | ✗ | HU window used to pack the CT to uint8. In the TS type as provenance metadata; not rendered by the UI. |
| `defaultWL` | `{window,level}` | ✓ | ✓ | Default CT window/level, **normalized 0..1**. Seeds the store. |
| `mriWL` | `{window,level}` optional? | ✓ | ✓ | W/L applied when MR is shown (`setDisplayMode`). |
| `hasSegmentation` | boolean | ✓ | ✓ | Whether a seg volume exists. Read from the manifest (`App.tsx`, `ControlRail.tsx`) to gate segmentation UI; also mirrored into `DatasetEntry`. |
| `labels` | `Record<string,string> \| null` | ✓ | ✓ | Label-int (as string) → display name. Drives per-label toggles + `labelVisible` seeding. |
| `labelColors` | `Record<string,[r,g,b]>` optional? | ✓ | ✓ | Label-int → RGB 0..255. Absent ⇒ `LABEL_PALETTE` fallback. Envelope builder sets this; single-tumour builders don't. |
| `clinicalNote` | string optional? | ✓ | ✓ | Free text in StatsPanel. |
| `timepoints` | `Timepoint[]` | ✓ | ✓ | See §2.1. |
| `meshes` | `null` | ✓ | ✗ | **Always `null` — dead field.** Live meshes are `timepoints[].meshes`. |
| `metrics` | `string \| null` | ✓ | ✓ | Filename of `metrics.json` (e.g. `"metrics.json"`) or `null`. If truthy, frontend fetches it. |

### 2.1 `timepoints[]` entry

The manifest holds **filenames** (strings); the decoded runtime `Timepoint` (`dataset.ts:49-59`) holds the fetched bytes/meshes.

| Field | Type | Notes |
|---|---|---|
| `id` | string | e.g. `"t0"`. |
| `label` | string | e.g. `"CT + MR T1"`. |
| `ct` | string | Filename of the CT volume (`"ct.bin.gz"`). Required. |
| `mri` | string optional? | MR volume filename; registered into the same grid. |
| `seg` | string optional? | Multi-label mask filename. |
| `meshes` | `{label:number, file:string}[]` optional? | **Preferred.** One isosurface file per seg label. |
| `tumorMesh` | string optional? | **Legacy.** Single mesh, hard-mapped to **label 2** by the viewer. |
| `organMesh` | string optional? | **Legacy.** Single mesh, hard-mapped to **label 1**. |

### 2.2 Sub-objects

- `defaultWL` / `mriWL`: `{ "window": number, "level": number }` — **normalized 0..1**, not HU. (`level` is the window center, `window` its full width, both in 0..1 luminance.)
- `storageWindowHU`: `{ "lo": number, "hi": number }` — HU (e.g. `{-200, 400}` for H&N).

### 2.3 Verbatim example (`hanseg_case_01_gt`)

```json
{
  "id": "hanseg_case_01_gt",
  "title": "HaN-Seg · case_01 · GROUND TRUTH (expert annotation)",
  "source": "HaN-Seg (Podobnik et al., Zenodo 7442914) — CT + T1 MR + OAR masks",
  "modality": "CT + MR · T1",
  "dims": [256, 256, 202],
  "worldExtent": [1.0, 1.0, 0.7075306479859895],
  "spacingMm": [2.2305, 2.2305, 2.0],
  "storageWindowHU": { "lo": -200, "hi": 400 },
  "defaultWL": { "window": 0.85, "level": 0.5 },
  "mriWL": { "window": 0.9, "level": 0.5 },
  "hasSegmentation": true,
  "labels": { "1": "body envelope", "4": "bone envelope", "3": "organ envelope", "2": "brainstem (ground truth)" },
  "labelColors": { "1": [90,140,200], "4": [222,216,198], "3": [150,110,205], "2": [90,200,110] },
  "clinicalNote": "Full envelope: body (CT), bone (CT), organ (union of all OARs), and the brainstem (ground truth) layer.",
  "timepoints": [{
    "id": "t0", "label": "CT + MR T1",
    "ct": "ct.bin.gz", "mri": "mri.bin.gz", "seg": "seg.bin.gz",
    "meshes": [
      { "label": 1, "file": "mesh1.json" }, { "label": 4, "file": "mesh4.json" },
      { "label": 3, "file": "mesh3.json" }, { "label": 2, "file": "mesh2.json" }
    ]
  }],
  "meshes": null,
  "metrics": null
}
```

---

## 3. `index.json` (dataset registry)

Built by `scripts/build_index.cjs` (`npm run data:index`) by scanning every `manifest.json`. Frontend type: `DatasetIndex` / `DatasetEntry` (`dataset.ts:131-147`).

```json
{
  "version": 1,
  "generatedFrom": "manifests",
  "count": 2,
  "datasets": [
    {
      "id": "hanseg_case_01_gt",
      "base": "/data/hanseg_case_01_gt",
      "title": "HaN-Seg · case_01 · GROUND TRUTH (expert annotation)",
      "organ": "organ envelope",
      "modality": "CT + MR · T1",
      "source": "HaN-Seg (Podobnik et al., Zenodo 7442914) — CT + T1 MR + OAR masks",
      "timepoints": 1,
      "hasSegmentation": true,
      "hasTumor": true
    }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `version` | number | Currently `1`. |
| `generatedFrom` | string | `"manifests"`. |
| `count` | number | `datasets.length`. |
| `datasets[]` | `DatasetEntry[]` | Rows below. |

**`DatasetEntry`:** `id`, `base` (`/data/<id>`), `title`, `organ` (string\|null), `modality`, `source`, `timepoints` (count), `hasSegmentation` (bool), `hasTumor` (bool). **Badge derivation follows the canonical label integers** (`build_index.cjs:30-42`, aligned with the frontend's `label === 2` = tumour invariant): `hasTumor` if label `2` exists (or a legacy `tumorMesh`); `organ` = the canonical organ label `3`, else the first non-tumour, non-`body`/`bone` label (covers legacy datasets where label 1 = kidney/lung). Earlier a label-name regex was used; it misclassified tumours whose names lacked a keyword (e.g. `brainstem (MedSAM2)`) — fixed 2026-07-09.

---

## 4. Binary volume assets (`*.bin.gz`)

| Property | Value |
|---|---|
| Payload | Raw voxel bytes, no header. |
| dtype | **uint8** (0..255). |
| Compression | gzip **level 6**, deterministic — `asset_common.write_gz` sets `mtime=0`, so identical data yields identical bytes across rebuilds. |
| Element count | `X · Y · Z` = `dims[0]·dims[1]·dims[2]`. |
| Ravel order | numpy C-order from `(Z,Y,X)` → **X fastest**. |
| **Index formula** | **`idx = x + X·(y + Y·z)`** where `X=dims[0]`, `Y=dims[1]`. |

Same formula in the pipeline (`preprocess_hn_mri.to_zyx` docstring) and the frontend (`dataset.ts:249`, `mpr.ts:30`). CT and MR volumes share the grid, so a single `vi` indexes both (enables fusion). Client-side gzip is only inflated when the bytes still carry the `1f 8b` magic (some hosts pre-inflate via `Content-Encoding`) — `dataset.ts:159-170`.

- **CT** (`ct.bin.gz`): CT HU linearly windowed by `storageWindowHU` to 0..255.
- **MR** (`mri.bin.gz`): robust-windowed (2nd–99.5th percentile of non-zero voxels) to 0..255.
- **seg** (`seg.bin.gz`): label integers per voxel (see §6). Painted body < bone < organ < tumour, so higher-precedence layers win shared voxels.

---

## 5. Mesh JSON (`mesh<label>.json`, `tumor.json`, `organ.json`)

```json
{ "positions": [x0,y0,z0, x1,y1,z1, ...], "indices": [i0,i1,i2, ...] }
```

| Key | On disk | Decoded (`MeshData`) | Notes |
|---|---|---|---|
| `positions` | flat JSON number array | `Float32Array` | Interleaved XYZ triples in **normalized world space** `[-worldExtent, +worldExtent]` per axis — the same mapping as `voxelToWorld`, so meshes land on the slices. |
| `indices` | flat JSON number array | `Uint32Array` | Triangle vertex indices (triples). |

No color and no normals are stored — **normals are computed client-side** (`computeVertexNormals`), and color comes from the seg label (§6). Produced by `mesh_from_mask` (Gaussian σ=0.6 → marching cubes at level 0.5; per-layer decimation `step`: body 3, bone 2, organ/tumour 1).

---

## 6. Labels & colors

**Canonical label integers** (envelope datasets — the current standard):

| Int | Layer | Default color (envelope builder) | Palette fallback |
|---|---|---|---|
| 1 | body | `[90,140,200]` blue | teal `[40,130,148]` |
| 2 | **tumour** | from `--tumour-color` (GT green `[90,200,110]`, seg amber `[255,150,70]`) | amber `[255,176,84]` |
| 3 | organ | `[150,110,205]` purple | purple `[170,120,210]` |
| 4 | bone | `[222,216,198]` bone-white | green `[120,200,120]` |

- **Label 2 = tumour is universal** across every builder.
- **Label 1 = body** in envelope datasets (the current convention). The only other builder, `preprocess_hn.py`, is single-tumour (label 2 only). Still: **read `labels`, don't hard-code — the label→name map is authoritative.**
- Color resolution (`labelColor`, `dataset.ts:78`): `manifest.labelColors[String(label)] ?? LABEL_PALETTE[(label-1) % LABEL_PALETTE.length]` (palette length is currently 5). `LABEL_PALETTE` (`dataset.ts:63-69`): `[40,130,148]`, `[255,176,84]`, `[170,120,210]`, `[120,200,120]`, `[230,120,150]`.
- Rendering: label 2 → solid glowing material; all other labels → translucent shells (`Viewer3D.tsx:86-105`). Seg overlay tint strength `SEG_TINT = 0.55`.

---

## 7. `metrics.json`

Written by `preprocess_hn_mri` (`"metrics": "metrics.json"`); envelope builder writes none (`"metrics": null`). Frontend type `RealMetrics` (`dataset.ts:39-47`).

| Field | Type | Notes |
|---|---|---|
| `tumorVolumeCm3` | number | From the native-resolution mask. |
| `maxDiameterMm` | number | Max bbox side. |
| `meanDiameterMm` | number | Mean of the three bbox sides. |
| `tumorVoxels` | number | Native voxel count. |
| `bboxMm` | number[3] | Bounding-box sides, order **`[dx, dy, dz]`**. |
| `organVolumeCm3` | number optional? | |
| `organLabel` | string optional? | |

---

## 8. Coordinate & grid conventions

The single most load-bearing contract; enforced by `scripts/geometry.py`. See [design.md §6](design.md#6-coordinate-system--the-alignment-contract).

| Concept | Rule |
|---|---|
| Reference grid | One per dataset, CT-derived: `256 × 256 × min(Z, 220)` (`OUT_XY=256`, `OUT_Z_CAP=220`). CT, MR, all masks resampled onto it. |
| Orientation | All inputs canonicalized to **LPS** at ingest. Grid must be axis-aligned; oblique is rejected. |
| World axes | X = L/R, Y = A/P, Z = S/I (slice / cranio-caudal). |
| Array order | `(Z, Y, X)` in numpy; raveled to `idx = x + X·(y + Y·z)`. |
| `dims` | `[X, Y, Z]`. |
| `worldExtent` | Half-width per axis, normalized so max physical axis = 1.0. `phys[i] = dims[i]·spacingMm[i]`; `worldExtent = phys / max(phys)`. |
| voxel→world | `world = ((v/(dim-1)) - 0.5)·2·worldExtent[axis]` (`voxelToWorld`). Meshes use the identical mapping. |
| world→voxel | `v = round((world/(2·worldExtent) + 0.5)·(dim-1))` (`mpr.realSampler`). |
| Display flip | Y flipped at render (`cy = Y-1-oy`) so +Y is up; stored volume is not flipped. |
| MPR planes | coronal U=X,V=Z · sagittal U=Y,V=Z · MIP projects along Y (`mpr.ts`). |

**Registration:** MR→CT via Mattes MI (rigid→affine), cached as `hanseg_data/registration_check/<case>/transform.tfm` (the transform only, reused by every builder). `mr_in_ct.nrrd` is the MR pre-resampled into the full-res CT grid (fast path for MedSAM2).

---

## 9. Pipeline CLI contracts (producers)

Dataset builders and their outputs. Full arg detail in each script's `--help`; canonical defaults here.

| Script | Key inputs | Key flags (default) | Outputs → `public/data/<id>/` |
|---|---|---|---|
| `build_envelope_dataset.py` | `--case-dir`, `--tumour` (NRRD) | `--organ-glob "*OAR_*.nrrd"`, `--bone-hu 200`, `--tumour-label "tumour"`, `--tumour-color "255,150,70"`, `--id`, `--title` | `ct/mri/seg.bin.gz`, `mesh{1,2,3,4}.json`, `manifest.json` (multi-label, `labelColors`, `metrics:null`) |
| `preprocess_hn_mri.py` | `--case-dir` | `--roi-glob "*OAR_Bone_Mandible*.nrrd"`, `--roi-label "mandible"`, `--id`, `--title` | `ct/mri/seg.bin.gz`, `tumor.json`, `metrics.json`, `manifest.json` (`labels:{2:…}`, `tumorMesh`) |
| `register_ct_mr.py` | `--case-dir` | `--out` | (to `hanseg_data/registration_check/<case>/`) `mr_in_ct.nrrd`, `transform.tfm`, `overlay_*.png`, `checker_after.png`, `mandible_on_MR.png`, `qa.json` |
| `medsam2_seed_test.py` | `--case-dir` OR (`--mr` + `--mask`) | `--modality {mr,ct}`, `--prompt {mask,box}`, `--oar Bone_Mandible`, `--crop-margin-mm 24`, `--surface` | (to `runs/medsam2_seed/…`) `pred_mask.nrrd`, `qa.png`, `metrics.json` |
| `triage_pipeline.py` | `--case-dir` | `--oar Brainstem`, `--modality mr`, `--ensemble 3`, `--dilate-mm 2`, `--converge-min 0.7`, `--coherence-min 0.9` | (to `runs/triage/…`) `envelope.nrrd`, `report.json` |
| `geometry.py` | `--case-dir` | — | (audit CLI; prints orientation + alignment, non-zero exit on misalignment) |
| `build_index.cjs` | scans `public/data/*/manifest.json` | — | `public/data/index.json` |

**Legacy builder:** `preprocess_hn.py` (DICOM + RTSTRUCT single-slice → `hn_<PID>/`, tumour = label 2, emits `tumorMesh`). It shares the pure asset helpers via `asset_common` (`mesh_from_mask`/`window_u8`/`write_gz`); only its numpy index-based grid resampling is bespoke (it works on numpy arrays from `rt_utils`, not SimpleITK images). The KiTS/NLST builders were removed 2026-07-09 (data no longer needed).

**seg volume `metrics.json` note:** `bboxMm` is ordered `[dx, dy, dz]` while arrays are `(z,y,x)` — the builder reorders explicitly (`preprocess_hn_mri.py:127`).

---

## 10. Known contract drift

Tracked in full in [design.md §8](design.md#8-known-drift--alignment-worklist). Schema-level summary:

1. **README `## How it works` asset/manifest example is stale** vs the real files (`ct_t*` naming, missing `mri`/`labelColors`/`meshes[]`/`storageWindowHU`/`mriWL`). This doc supersedes it.
2. **Vestigial field:** top-level `meshes` is always `null` (the live mesh list is `timepoints[].meshes`). `storageWindowHU` is emitted-but-unrendered, now acknowledged in the TS type as provenance metadata (no longer a type drift).
6. ~~Picker badges misclassify the ML case.~~ **RESOLVED 2026-07-09** — `build_index.cjs` now derives badges from canonical label ints (`label 2` = tumour, `3` = organ), aligned with the frontend's `label === 2` invariant. `index.json` regenerated.
3. **Two mesh mechanisms:** legacy `tumorMesh`/`organMesh` (labels 2/1) vs `timepoints[].meshes[]` (preferred).
4. **Partial runtime validation** — `dataset.ts::validateManifest` checks every field the app dereferences unconditionally (`modality`, `dims`, `worldExtent`, `spacingMm`, `defaultWL`, `timepoints[].ct`) with clear errors; optional/nullish-guarded fields (`mriWL`, `labels`, `title`…) are still trusted.
5. **Label 1 is not stable across builders** — read `labels`.
