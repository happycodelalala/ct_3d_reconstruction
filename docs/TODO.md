# ONCOVOL — Alignment & Cleanup TODO

Actionable worklist derived from the [design.md](design.md) / [schema.md](schema.md) review (2026-07-09). Each item is a real finding against the current codebase, with root cause and proposed fix. Priorities: **P1** = correctness/UX bug · **P2** = dedup & alignment (prevents future divergence) · **P3** = nice-to-have.

Companion tracking: this list is the actionable form of [design.md §8](design.md#8-known-drift--alignment-worklist) and [schema.md §10](schema.md#10-known-contract-drift). Keep them in sync (or make those sections point here).

---

## P1 — Bugs

- [ ] **Picker badges misclassify the ML dataset.** `public/data/index.json` currently records `hanseg_case_01_seg` as `"hasTumor": false` with `"organ": "brainstem (MedSAM2)"` — but that layer *is* the tumour.
  - **Root cause:** `build_index.cjs:35-37` derives `hasTumor`/`organ` from a label-**name** regex (`/tumou?r|lesion|gtv|segmentation|ground.?truth/i`). The GT case only passes because its label is named `…(ground truth)`; the ML tumour named `brainstem (MedSAM2)` matches nothing. Compounded by JS numeric key ordering (label 2 sorts before label 3 in `Object.values`), the tumour is then picked as the `organ` badge.
  - **Fix:** for envelope datasets, derive badges from the **canonical label integers** (2 = tumour, 3 = organ, 1 = body, 4 = bone) instead of name matching. Fall back to the name heuristic only when labels are absent. Then rerun `npm run data:index` and verify `hanseg_case_01_seg → hasTumor:true, organ:"organ envelope"`.
  - **Refs:** `scripts/build_index.cjs:35-37`, `public/data/index.json:21,26`.

---

## P2 — Deduplication & alignment

- [ ] **Consolidate the two builder families.** `preprocess_hn.py`, `preprocess_nlst.py`, `preprocess_nlst_tumor.py`, and `preprocess.cjs` each re-implement the output grid, windowing, meshing, and gzip that `preprocess_hn_mri` already exports (`output_grid`, `window_ct_u8`, `mesh_from_mask`, the gzip-6 write).
  - **Fix:** migrate the Python legacy builders onto the shared helpers as they're next touched; leave `preprocess.cjs` (KiTS, Node) unless it's being reworked. Target: one canonical grid/window/mesh implementation.
  - **Refs:** [design.md §3.2–3.3](design.md#3-the-python-pipeline).

- [ ] **Standardize on `timepoints[].meshes[]`; retire the legacy mesh slots for new datasets.** Two mesh-carrying mechanisms coexist: legacy `tumorMesh`/`organMesh` (hard-mapped to labels 2/1 in `Viewer3D`) vs the current per-label `meshes[]`.
  - **Fix:** new builders emit only `meshes[]` + `labelColors` (the envelope path). Keep the legacy slots readable for old datasets, but don't produce them. `preprocess_hn_mri` still emits `tumorMesh` — move it to `meshes[]` when convenient.
  - **Refs:** `schema.md §2.1`, `src/components/Viewer3D.tsx:76-82`.

- [ ] **Decide fate of dead manifest fields.** `storageWindowHU` (written by every builder, absent from the TS `Manifest` type, never read) and top-level `meshes` (always `null`).
  - **Fix:** either drop them from the builders, or keep and explicitly document as reproducibility metadata. Pick one and reflect it in `schema.md`.
  - **Refs:** `schema.md §2`, `scripts/preprocess_hn_mri.py:161,172`, `scripts/build_envelope_dataset.py:132,145`.

- [ ] **Unify the label-1 convention across builders.** Label 2 = tumour is universal, but label 1 = "body" in envelope datasets vs "kidney"/"lung"/organ in legacy builders.
  - **Fix:** migrate legacy builders to the envelope label scheme (1=body, 2=tumour, 3=organ, 4=bone) as they're consolidated; until then, all consumers must read `labels` and never assume label 1.
  - **Refs:** `schema.md §6`.

---

## P2 — Documentation alignment

- [ ] **Trim README `## How it works` / `### Unified manifest` to point at `schema.md`.** The README asset block documents stale names (`ct_t*.bin.gz`, `tumor_t*.json`, `organ_t*.json`) and a manifest example missing `mri`, `labelColors`, `timepoints[].meshes[]`, `storageWindowHU`, `mriWL`. `schema.md` is now the source of truth.
  - **Refs:** `README.md` (`## How it works`), `schema.md §1–2`.

- [ ] **Fix README `## Project layout` drift.** `App.tsx` is listed under `components/` but lives in `src/` root; `PatientPicker.tsx` is missing; the `scripts/` list omits `build_envelope_dataset.py`, `triage_pipeline.py`, `calibration_experiment.py`, and `geometry.py`.
  - **Refs:** `README.md` (`## Project layout`).

---

## P3 — Improvements

- [ ] **Add lightweight load-time manifest validation** in `src/lib/dataset.ts` (`loadDataset` currently blind-casts `as Manifest`). A minimal check on required fields (`dims`, `defaultWL`, `timepoints[].ct`) turns silent deep-`undefined` failures into a clear error.
  - **Refs:** `src/lib/dataset.ts:180`.

- [ ] **Reconcile the split windowing conventions** — `defaultWL`/`mriWL` (normalized 0..1, UI) vs `storageWindowHU` / per-builder HU windows (preprocess). Document the relationship clearly, or derive one from the other.
  - **Refs:** `schema.md §2.2, §4`.

- [ ] **(Optional) Wire `scripts/geometry.py --case-dir` into the build path** as a pre-flight orientation check, so a misaligned case fails before a dataset is built rather than after.
  - **Refs:** `scripts/geometry.py`, [design.md §6](design.md#6-coordinate-system--the-alignment-contract).

---

## Done in this review (2026-07-09)

Doc inaccuracies found by adversarial verification against the code and already corrected:

- [x] **`hasSegmentation` mislabeled as "never read."** It *is* read from the manifest (`App.tsx:40`, `ControlRail.tsx:70`) to gate segmentation UI. Fixed in `schema.md §2/§10` and `design.md §8.3`.
- [x] **`bboxMm` reorder cite** corrected `preprocess_hn_mri.py:122` → `:127` (`schema.md §9`).
- [x] **Palette fallback** softened `% 5` → `% LABEL_PALETTE.length` (`schema.md §6`).
- [x] Verified (CONFIRMED) the remaining ~17 load-bearing claims: gzip-6, `idx = x + X·(y + Y·z)`, uint8, dead `storageWindowHU`/`meshes`, no runtime validation, label convention, palette/colors, thresholds & paint precedence, grid dims, coordinate axes, MIP-along-Y, registration cache path, `metrics.json` write, the full import/dedup graph, and all CLI defaults.
