# ONCOVOL — System Design

**Status:** source of truth for architecture and module responsibilities.
**Companion:** [schema.md](schema.md) pins the exact data contracts (manifest, assets, meshes, coordinates). Read that for field-level detail; read this for how the pieces fit and why.

This document exists to **deduplicate and align** the project: one place that says what each module owns, how data flows, and which conventions are load-bearing — so new work extends the system instead of re-deriving or diverging from it. Where a topic already has a deep doc, this file links rather than repeats (see [§9](#9-related-docs)).

---

## 1. What the system is

ONCOVOL turns paired **CT + MRI** studies into an interactive 3D reconstruction in the browser: axial/MPR slice views, fused CT/MR, and toggleable 3D shells (body, bone, organ, tumour). It has two halves joined by a single file-based contract:

```
   ┌─────────────────────────── Python / Node pipeline ───────────────────────────┐
   │  raw scans (NRRD / DICOM / NIfTI)                                             │
   │        │  register · resample · window · threshold · segment · mesh          │
   │        ▼                                                                      │
   │  public/data/<id>/  { manifest.json, *.bin.gz, mesh*.json, index.json }  ─────┼──┐
   └───────────────────────────────────────────────────────────────────────────────┘  │
                                                                                        │ static assets
   ┌─────────────────────────── Browser workstation (Vite + React + R3F) ──────────┐  │
   │  loadIndex → picker → loadDataset → zustand store → { CT panel, MPR strip,    │◀─┘
   │  3D viewer, stats } — all rendering off the decoded uint8 volumes + meshes    │
   └───────────────────────────────────────────────────────────────────────────────┘
```

**The asset boundary is the contract.** The pipeline emits static files; the frontend only ever reads them. Neither side shares code or types with the other — [schema.md](schema.md) is the only thing that keeps them in sync. There is no server, and only the load-bearing manifest fields are validated at load (`dataset.ts::validateManifest`), so the schema being written down and honored *is* the integration test.

**Tech stack.** Frontend: Vite 5, React 18, Three.js 0.168 via @react-three/fiber + drei, zustand 4 for state, TypeScript strict. Pipeline: Python 3 + SimpleITK + scikit-image (imaging), Node for the index builder. MedSAM2 (vendored) for promptable tumour segmentation; TotalSegmentator for organ masks.

---

## 2. Data flow (end to end)

1. **Ingest & canonicalize.** Read CT/MR/masks (SimpleITK). Every image is reoriented to **LPS** on load (`geometry.canonicalize`) so the rest of the pipeline can assume an axis-aligned frame. Oblique volumes are rejected loudly. → [§6](#6-coordinate-system--the-alignment-contract)
2. **Register.** MR → CT with Mattes mutual information (rigid → affine), cached as a `.tfm` (`register_ct_mr`). One transform per case, reused everywhere.
3. **Resample to a shared grid.** CT, registered MR, and every mask are resampled onto ONE reference grid derived from the CT (`preprocess_hn_mri.output_grid`, 256×256×min(Z,220)). This is what makes slices, overlays, and meshes co-register.
4. **Derive layers.** Body/bone from CT thresholds; organ from OAR masks (expert) or TotalSegmentator (ML); tumour from an expert mask, MedSAM2, or the recall-safe triage envelope.
5. **Window & serialize.** CT/MR windowed to uint8, gzipped; masks painted into one multi-label `seg` volume; each layer meshed (marching cubes) into normalized world space.
6. **Manifest + index.** Write `manifest.json` per dataset; `build_index.cjs` scans all manifests into `public/data/index.json` for the picker.
7. **Load & render.** Browser fetches the index, then a dataset's manifest + volumes + meshes; the store fans state out to the 2D/MPR/3D views.

---

## 3. The Python pipeline

### 3.1 Module responsibilities

| Module | Owns | Key exports |
|---|---|---|
| `asset_common.py` | Dependency-light shared helpers — **pure numpy + scikit-image, no SimpleITK** — so every builder (incl. the numpy legacy one) and the MedSAM2 preprocessing share them. `window_u8` is the single window-to-uint8 primitive (`window_ct_u8`/`window_mr_u8`/`mr_to_uint8`/`ct_to_uint8` all delegate to it). | `mesh_from_mask`, `window_u8`, `write_gz` |
| `geometry.py` | Orientation guardrails & auto-alignment (LPS canonicalization, axis-aligned assertion, physical-overlap checks) + a `--case-dir` audit CLI. | `canonicalize`, `assert_axis_aligned`, `assert_same_frame`, `masks_overlap_grid`, `warn_if_no_overlap` |
| `register_ct_mr.py` | MR→CT registration (MI, rigid+affine), the `.tfm` cache, QA overlays, and `mr_in_ct.nrrd`. | `load_ct_mr`, `register`, `register_cached`, `transform_cache_path` |
| `preprocess_hn_mri.py` | The SimpleITK output grid + cached-registration setup + the single-tumour CT+MR dataset builder (pure asset helpers now live in `asset_common`). | `output_grid`, `to_zyx`, `prepare_output_volumes`, `window_ct_u8`/`window_mr_u8` |
| `build_envelope_dataset.py` | The multi-label **envelope** dataset builder (body/bone/organ/tumour, per-label colors + meshes). | (CLI) |
| `medsam2_seed_test.py` | Promptable single-seed→3D MedSAM2 engine + seed-test harness. | `prepare_case`, `segment`, `dice` |
| `triage_pipeline.py` | Recall-safe tumour-envelope triage (ensemble → consensus → dilate → prune → route). | `recall_safe_envelopes`, `recall_precision` |
| `build_index.cjs` | Scans manifests → `public/data/index.json` (the picker registry). | (CLI, `npm run data:index`) |
| `preprocess_hn.py` | Legacy CT-only single-slice H&N builder (DICOM + RTSTRUCT). Shares `asset_common`; only its numpy index-based grid resampling is bespoke. | (CLI) |
| `e2e.mjs`, `e2e_setup.sh` | Headless Puppeteer smoke test of the real app. | (CLI, `npm run e2e`) |

### 3.2 Shared-helper graph (the dedup structure)

Pure asset helpers live in `asset_common` (no SimpleITK), so **every** builder shares them — including the numpy-based legacy `preprocess_hn.py`. The SimpleITK grid + registration chain layers above.

```
asset_common.py ─(mesh_from_mask, window_u8, write_gz) ── shared by ALL builders + MedSAM2 prep
geometry.py ─────(canonicalize, asserts, overlap) ───────┐
     ▲                                                    │
register_ct_mr.py ──(load_ct_mr, register_cached)         │
     ▲                                                    │
preprocess_hn_mri.py ──(output_grid, prepare_output_volumes) ──┤
     ▲                        ▲                           │
build_envelope_dataset.py     │                           │
                              │                           │
medsam2_seed_test.py ──(prepare_case, segment, dice) ─────┘
     ▲
triage_pipeline.py

preprocess_hn.py (legacy, numpy) — imports asset_common; only its numpy index-based
grid resampling is its own (it can't use the SimpleITK output_grid).
```

**Rule of thumb for new imaging code:** enter through `load_ct_mr` (gets you canonicalization + cached registration for free) and build on `preprocess_hn_mri`'s `prepare_output_volumes` / `mesh_from_mask`. Do not re-implement the grid, windowing, meshing, or gzip — that is exactly the duplication this doc exists to prevent.

### 3.3 Two builder families

- **Single-tumour builders** (`preprocess_hn_mri`, and the legacy `preprocess*`): one organ + one tumour, meshes carried in the legacy `tumorMesh`/`organMesh` slots, no `labelColors`.
- **Envelope builder** (`build_envelope_dataset`): the current shape — a multi-label `seg` (body/bone/organ/tumour), one `mesh{label}.json` per layer via `timepoints[].meshes[]`, explicit `labelColors`. **New datasets should use this path.** The two live front-end datasets (`hanseg_case_01_gt`, `hanseg_case_01_seg`) are both envelope datasets.

---

## 4. The browser workstation

### 4.1 File responsibilities

| File | Owns |
|---|---|
| `src/lib/dataset.ts` | The dataset registry + loader, all TS types (`Manifest`, `Timepoint`, …), the normalized voxel↔world mapping, slice rendering + windowing + seg tinting, the label→color resolution. |
| `src/lib/mpr.ts` | World-space sampler; coronal/sagittal/oblique reformatting; coronal MIP. Defines the anatomical plane axes. |
| `src/lib/sliceTexture.ts` | Wraps an `ImageData` slice/reformat as a Three.js texture. |
| `src/store.ts` | zustand app state (dataset, timepoint, slice, crosshair, W/L, `labelVisible`, display mode) + actions. Seeds per-label visibility and W/L from the manifest on load. |
| `src/components/PatientPicker.tsx` | Dataset/patient switcher (reads `index.json`). |
| `src/components/CTPanel.tsx` | 2D axial CT viewer + crosshair + scrubber. |
| `src/components/MPRStrip.tsx` | Coronal / sagittal / oblique / MIP reformat tiles. |
| `src/components/Viewer3D.tsx` | R3F scene: per-label meshes, cut-plane, MPR ortho box; unifies legacy + `meshes[]`; label→material. |
| `src/components/ControlRail.tsx` | Per-label visibility toggles + CT window/level. |
| `src/components/StatsPanel.tsx`, `Timeline.tsx` | Metrics/acquisition info; timepoint scrubber + playback. |

### 4.2 How label → color → mesh works (single source)

Color is driven **entirely by the seg label integer**, never stored in the mesh JSON. `labelColor(manifest, label)` resolves `manifest.labelColors[label]` if present, else falls back to `LABEL_PALETTE[(label-1) % 5]`. `buildLabelStyle` filters that by `labelVisible`. `Viewer3D` maps each mesh's label through the same resolver and picks a material (label 2 = solid glowing tumour; others = translucent shells). This means: **to restyle a layer, change `labelColors` in the manifest — nothing in the frontend.** → [schema.md §6](schema.md#6-labels--colors).

---

## 5. Segmentation & the model roles

- **Body / bone** — pure CT thresholds (`HU > -500` largest-CC fill-holes for body; `HU > 200 ∩ body` for bone). No ML.
- **Organ** — union of OAR masks: expert annotations (HaN-Seg) for the ground-truth case, **TotalSegmentator** (nnU-Net, Apache-2.0 H&N tasks) for the ML case.
- **Tumour** — the only ML-segmented target. Options: an expert mask (GT case), **MedSAM2** promptable single-seed→3D propagation, or the **recall-safe triage envelope** (`triage_pipeline`).
- **MedSAM2's role** is narrow: promptable propagation of ONE seed slice into a 3D tumour mask. It does not do body/bone/organ. Validated mechanism: Dice ≈0.89 (CT mask-seed, tight ROI crop) on the mandible proxy. → [medsam2-setup.md](medsam2-setup.md), [medsam2-seed-test-plan.md](medsam2-seed-test-plan.md)
- **Triage confidence pivot.** The original plan used ensemble-variance uncertainty to flag errors; it was tested and **failed** (AUROC ≈0.50 — variance is blind to systematic under-segmentation). The system instead ships a **recall-safe dilated envelope + coverage/coherence routing** for human review. → [tumour-triage-pipeline.md](tumour-triage-pipeline.md)

---

## 6. Coordinate system — the alignment contract

This is the most load-bearing convention in the system and the reason [geometry.py](../scripts/geometry.py) exists. Full detail in [schema.md §8](schema.md#8-coordinate--grid-conventions); the essentials:

- **One grid per dataset.** CT, registered MR, and all masks share the reference grid (CT-derived, 256×256×min(Z,220)). Cross-modality alignment is by construction, not by luck.
- **Axis-aligned only.** The frontend renders each volume as a plain index cube with **no direction cosines** — meshes and slices co-register in normalized index space. That is correct *only* because every input is canonicalized to LPS at ingest. Oblique data is rejected rather than silently mislabeled.
- **World axes:** X = L/R, Y = A/P, Z = S/I (slice/cranio-caudal axis). Assumed by `mpr.planeBasis` and the 3D scene.
- **Storage layout:** uint8, gzip, C-order `(Z,Y,X)` → **`idx = x + X*(y + Y*z)`** (X fastest). `dims = [X, Y, Z]`.
- **`worldExtent`** = per-axis half-width, normalized so the largest physical axis = 1.0. Mesh vertices use the identical mapping (`voxelToWorld`), which is why they land on the slices.
- **Display Y-flip** (`cy = Y-1-oy`) is applied at render time only; the stored volume is not flipped.

**Guardrails now enforce this:** `canonicalize` (auto-fix flips/permutations), `assert_axis_aligned` (reject oblique), `warn_if_no_overlap` (flag annotations in a mismatched physical frame). Audit any case with `python scripts/geometry.py --case-dir <case>`.

---

## 7. Key design decisions

- **File-based asset boundary, no server.** Simple, cacheable, statically hostable. Cost: no *server-side* validation — the schema doc is the contract; the loader guards the load-bearing manifest fields at fetch time (`dataset.ts::validateManifest`), but optional fields are still trusted. → [§8](#8-known-drift--alignment-worklist)
- **Everything on one grid.** Trades native resolution (256² in-plane) for guaranteed cross-modality/mesh registration and light assets. Rendering the native 1024² envelope would need substantially more GPU/memory; 256³ is the deliberate default.
- **Label color in the manifest, not the mesh.** Data-driven theming; one place to change a layer's look.
- **LPS canonicalization at ingest.** Turns the frontend's axis-aligned assumption from "happens to hold" into "enforced," so future non-LPS data can't silently corrupt orientation.
- **Recall-safe over precise.** For triage, a high-recall envelope a human confirms beats a precise-but-holed contour; confidence comes from coverage/coherence, not from (disproven) variance uncertainty.

---

## 8. Known drift & alignment worklist

These are the concrete deduplication/alignment targets this doc-pair is meant to drive. Each is real today.

1. **README asset block is stale.** `## How it works` documents `ct_t*.bin.gz` / `tumor_t*.json` / `organ_t*.json`; on disk it is `ct.bin.gz` / `mri.bin.gz` / `mesh{label}.json`. → point the README at [schema.md](schema.md) and trim.
2. **README manifest example is missing fields** now emitted: `storageWindowHU`, `mriWL`, `labelColors`, `timepoints[].mri`, `timepoints[].meshes[]`.
3. **Vestigial manifest field.** Top-level `meshes` is always `null` (the live mesh list is `timepoints[].meshes`); kept as a harmless vestige rather than churning six builders. `storageWindowHU` is emitted-but-unrendered provenance, now acknowledged in the TS `Manifest` type (drift resolved). Manifest `hasSegmentation` *is* read — `App.tsx`/`ControlRail.tsx` gate the segmentation UI on it — so it stays.
4. **Two mesh-carrying mechanisms.** Legacy `tumorMesh`/`organMesh` (hard-mapped to labels 2/1) vs the current `timepoints[].meshes[]`. New builders should emit only `meshes[]`; the legacy slots stay for old datasets.
5. ~~Builders duplicate primitives.~~ **RESOLVED 2026-07-09.** KiTS/NLST builders removed; the verbatim `mesh_from_mask` duplicate + repeated window/gzip patterns extracted to `asset_common.py` (pure numpy/skimage, no SimpleITK), now shared by `preprocess_hn_mri`, `build_envelope_dataset`, and the legacy `preprocess_hn.py`. Verified byte-identical envelope rebuild; `write_gz(mtime=0)` also makes builds reproducible. Only `preprocess_hn.py`'s numpy grid resampling stays bespoke (it can't use the SimpleITK `output_grid`).
6. **Label-1 semantics vary.** Label 2 = tumour is universal; label 1 = "body" in envelope datasets but "kidney"/"lung"/organ in legacy builders. Documented canonically in [schema.md](schema.md#labels--colors) — don't assume label 1 without checking `labels`.
7. ~~No runtime manifest validation.~~ **RESOLVED 2026-07-09.** `dataset.ts::validateManifest` now checks the unconditionally-dereferenced fields at load (via a shared `fetchJson` guard), so a malformed or unavailable manifest fails with an actionable message instead of a deep `undefined` access or a cryptic 404 `SyntaxError`. (Not a full schema validator — it covers the load-bearing fields, not every optional one.)
8. **Windowing conventions are split** (`defaultWL`/`mriWL` normalized 0..1 for the UI vs `storageWindowHU`/per-builder HU windows at preprocess). Documented, not yet unified.
9. ~~Picker badge derivation is fragile.~~ **RESOLVED 2026-07-09.** `build_index.cjs` derived `hasTumor`/`organ` from a label-_name_ regex, so the ML dataset's tumour (`brainstem (MedSAM2)`) was mislabelled (`hasTumor:false`, tumour picked as `organ`). Now derives from the canonical label integers (2 = tumour, 3 = organ), aligned with the frontend's `label === 2` invariant. Follow-up (open): converge `StatsPanel`'s separate tumour-detection path too — see [TODO.md](TODO.md).

---

## 9. Related docs

| Doc | Scope |
|---|---|
| [schema.md](schema.md) | **Field-level data contracts** (manifest, index, assets, meshes, metrics, coordinates, CLI I/O). The companion to this file. |
| [ct-mri-tumour-segmentation.md](ct-mri-tumour-segmentation.md) | The flagship "why + how" for CT+MR fusion and tumour segmentation. |
| [head-and-neck-segmentation.md](head-and-neck-segmentation.md) | The CT-only single-slice fallback pipeline. |
| [medsam2-setup.md](medsam2-setup.md) | MedSAM2 install + usage runbook. |
| [medsam2-seed-test-plan.md](medsam2-seed-test-plan.md) | The seed-test rationale + measured results. |
| [tumour-triage-pipeline.md](tumour-triage-pipeline.md) | Triage design, the failed uncertainty assumption, the recall-safe pivot. |
| [../README.md](../README.md) | User-facing quick start + per-dataset regeneration recipes. |
