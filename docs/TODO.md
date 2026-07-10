# ONCOVOL — Alignment & Cleanup TODO

Actionable worklist derived from the [design.md](design.md) / [schema.md](schema.md) review (2026-07-09). Each item is a real finding against the current codebase, with root cause and proposed fix. Priorities: **P1** = correctness/UX bug · **P2** = dedup & alignment (prevents future divergence) · **P3** = nice-to-have.

Companion tracking: this list is the actionable form of [design.md §8](design.md#8-known-drift--alignment-worklist) and [schema.md §10](schema.md#10-known-contract-drift). Keep them in sync (or make those sections point here).

---

## P1 — Bugs

- [x] **Picker badges misclassify the ML dataset. — FIXED 2026-07-09.** `public/data/index.json` recorded `hanseg_case_01_seg` as `"hasTumor": false` with `"organ": "brainstem (MedSAM2)"` — but that layer *is* the tumour.
  - **Root cause:** `build_index.cjs` derived `hasTumor`/`organ` from a label-**name** regex (`/tumou?r|lesion|gtv|segmentation|ground.?truth/i`). The GT case only passed because its label was named `…(ground truth)`; the ML tumour named `brainstem (MedSAM2)` matched nothing. Compounded by JS numeric key ordering (label 2 sorts before label 3 in `Object.values`), the tumour was then picked as the `organ` badge. The deeper cause: `build_index` diverged from the `label === 2` = tumour invariant the rest of the system (`Viewer3D`, MIP) already relies on; the `segmentation|ground.?truth` keywords were a patch layered on that divergence.
  - **Resolution:** badges now derive from the **canonical label integers** (2 = tumour, 3 = organ, 1 = body, 4 = bone), with name/`tumorMesh` only as a legacy fallback. Verified across all builder schemes (envelope, KiTS, NLST, single-tumour) with no regression; `index.json` regenerated → both datasets `hasTumor:true, organ:"organ envelope"`.
  - **Refs:** `scripts/build_index.cjs:30-42`.

---

## P2 — Deduplication & alignment

- [x] **Consolidate the two builder families. — RESOLVED by removal 2026-07-09.** With KiTS/NLST no longer needed, the root-cause fix for the legacy-builder duplication was **deletion, not consolidation**: removed `preprocess.cjs`, `preprocess_nlst.py`, `preprocess_nlst_tumor.py` (+ the `data:kits` npm script) and their README/docs references, and the dead `lungVolumeCm3` frontend plumbing they fed (types, loader, StatsPanel). Verified: no `src` refs remain, `tsc` green. Only `preprocess_hn.py` (CT-only single-slice H&N) still duplicates the primitives — tracked below.
  - **Also RESOLVED 2026-07-09:** the pure-helper duplication (`mesh_from_mask` verbatim + window/gzip patterns) is extracted to `asset_common.py` (numpy/skimage, no SimpleITK), shared by all three builders including `preprocess_hn.py`. Verified byte-identical envelope rebuild (meshes + decompressed volumes); `write_gz(mtime=0)` makes builds reproducible. Only `preprocess_hn.py`'s numpy grid resampling stays bespoke — it can't use the SimpleITK `output_grid`, so it's a legitimately-different implementation, not duplication.
  - **Follow-up sweep (same day):** a self-review caught 3 more inlined copies of the window-to-uint8 math — `window_mr_u8` and MedSAM2's `mr_to_uint8`/`ct_to_uint8` — that the first cut missed. All now delegate to `asset_common.window_u8` (numerically verified identical incl. out-of-range edges; envelope rebuild byte-identical). `window_u8` is now the single window-to-uint8 primitive.
  - **Refs:** `scripts/asset_common.py`, [design.md §8.5](design.md#8-known-drift--alignment-worklist).

- [ ] **Standardize on `timepoints[].meshes[]`; retire the legacy mesh slots for new datasets.** Two mesh-carrying mechanisms coexist: legacy `tumorMesh`/`organMesh` (hard-mapped to labels 2/1 in `Viewer3D`) vs the current per-label `meshes[]`.
  - **Fix:** new builders emit only `meshes[]` + `labelColors` (the envelope path). Keep the legacy slots readable for old datasets, but don't produce them. `preprocess_hn_mri` still emits `tumorMesh` — move it to `meshes[]` when convenient.
  - **Refs:** `schema.md §2.1`, `src/components/Viewer3D.tsx:76-82`.

- [x] **Decide fate of dead manifest fields. — DONE 2026-07-09.** `storageWindowHU` (emitted by every builder, was absent from the TS type) is genuine provenance → **added to the `Manifest` type** as optional metadata (`src/lib/dataset.ts`), resolving the type/data drift. Top-level `meshes` (always `null`) is a harmless vestige of the pre-per-timepoint schema → **kept and documented** rather than churn six builders for a null. Reflected in `schema.md §2/§10`, `design.md §8.3`.

- [x] **Unify the label-1 convention across builders. — RESOLVED 2026-07-09.** The builders that used label 1 for a non-body structure (KiTS kidney, NLST lung) were removed. Remaining builders: envelope (1=body, 2=tumour, 3=organ, 4=bone) and `preprocess_hn.py` (label 2 only). Consumers should still read `labels` rather than hard-code, but the cross-builder label-1 conflict is gone.
  - **Refs:** `schema.md §6`.

- [ ] **Converge the two remaining tumour-detection paths onto `label === 2`.** "Is there a tumour" is decided two ways — `Viewer3D.tsx:90`/`mpr.ts:145` (`label === 2`, canonical) and `StatsPanel.tsx:11` (`metrics && tumorMesh`, legacy). Not a bug (only single-tumour datasets have `metrics`+`tumorMesh`), but a divergent path that will mislead the next change.
  - **Partly resolved 2026-07-09:** the `StatsPanel` `labels["1"]`=organ landmine and its `hasOrgan`/`lungVolumeCm3` branch were **removed** with the NLST cleanup, so the acquisition panel is now label-agnostic. Only the `metrics && tumorMesh` presence check remains divergent.
  - **Fix:** when `StatsPanel` is next touched, gate the lesion panel on `label === 2` in the seg (or a seg-derived signal) rather than `tumorMesh`.
  - **Refs:** `src/components/StatsPanel.tsx:11`.

---

## P2 — Documentation alignment

- [x] **Trim README `## How it works` / `### Unified manifest` to point at `schema.md`. — DONE 2026-07-09.** Corrected the stale asset block (real filenames + canonical `1=body 2=tumour 3=organ 4=bone` labels; noted names are manifest-declared) and replaced the drifted full-manifest jsonc (an old NLST example missing `mri`/`labelColors`/`meshes[]`/`mriWL`) with an abridged current example that points at `docs/schema.md` as the full contract and `docs/design.md` for architecture.
  - **Refs:** `README.md` (`## How it works`, `### Unified manifest`).

- [x] **Fix README `## Project layout` drift. — DONE 2026-07-09.** Moved `App.tsx`/`main.tsx` to `src/` root, added `PatientPicker.tsx`, refreshed the `scripts/` list (added `build_envelope_dataset.py`, `medsam2_seed_test.py`, `triage_pipeline.py`, `build_index.cjs`; grouped the legacy builders), and corrected the stale contributor instruction (there is **no `DATASETS` array** — datasets register via `npm run data:index` scanning manifests). Root cause: README predated the manifest-scanned index registry.
  - **Refs:** `README.md` (`## Project layout`).

---

## P3 — Improvements

- [x] **Add lightweight load-time manifest validation. — DONE 2026-07-09.** `loadDataset` blind-cast `as Manifest`, so a malformed manifest failed later with a deep `undefined` access (e.g. `defaultWL.window`). Added `validateManifest` checking **every field the app dereferences unconditionally** — enumerated exhaustively from the code (grep), not from memory: `modality` (`.split`), `dims`, `worldExtent`, `spacingMm` (`.map`/`[i]`), `defaultWL`, non-empty `timepoints[]` each with a `ct`. (A self-review pass caught that a first cut had missed `spacingMm`/`modality`, which would have crashed StatsPanel/CTPanel — fixed before commit.)
  - **Similar issue found & fixed (same class as the audit bug — an inconsistently-applied guard):** `loadDataset`'s manifest and metrics fetches skipped the `res.ok` check that `fetchGzBin`/`fetchMesh`/`loadIndex` all have, so an unavailable dataset (the documented "DATASET UNAVAILABLE" case) threw a cryptic JSON `SyntaxError` on the 404 body. Root cause: the fetch-then-check pattern was duplicated across four sites and `loadDataset` got neither guard. Deduplicated into a `fetchJson` helper (fetch + `res.ok`) now used by manifest/metrics/mesh; `fetchMesh` also guards `positions[]`/`indices[]`.
  - **Verified:** `tsc` green; real gt/seg manifests accepted, malformed variants (missing dims / bad defaultWL / empty timepoints / tp without ct) rejected with `…/manifest.json: <reason> (see docs/schema.md)`.
  - **Refs:** `src/lib/dataset.ts` (`fetchJson`, `validateManifest`, `loadDataset`).

- [ ] **Reconcile the split windowing conventions** — `defaultWL`/`mriWL` (normalized 0..1, UI) vs `storageWindowHU` / per-builder HU windows (preprocess). Document the relationship clearly, or derive one from the other.
  - **Refs:** `schema.md §2.2, §4`.

- [ ] **(Optional) Wire `scripts/geometry.py --case-dir` into the build path** as a pre-flight orientation check, so a misaligned case fails before a dataset is built rather than after.
  - **Refs:** `scripts/geometry.py`, [design.md §6](design.md#6-coordinate-system--the-alignment-contract).

---

## P1 — Bugs (self-review of this session's implementation)

- [x] **`Viewer3D` MPR box divided by an unguarded `sliceMax` (0/0 → NaN). — FIXED 2026-07-09.** `Viewer3D.tsx:171` computed crosshair z as `slice / sliceMax` while the three sibling sites in `MPRStrip` all clamp with `Math.max(1, sliceMax)`. On a single-slice volume (`dims[2]===1` → `sliceMax=0`) it yields `0/0 = NaN`, which `realSampler`'s `k<0 || k>=Z` bounds check does **not** catch (NaN comparisons are false) → the MPR ortho box renders garbage/blank instead of a valid reslice. Latent (no current dataset is single-slice).
  - **Root cause:** the `slice → normalized z` expression was **duplicated across 4 sites**; the earlier div-by-zero pass guarded the 3 `MPRStrip` copies and missed the `Viewer3D` one. Patching a 4th copy would leave the same drift risk.
  - **Resolution:** extracted one guarded helper `store.ts::sliceFraction(slice, sliceMax)` used by all 4 sites — fixes the bug and removes the duplication that caused it. Verified `tsc` green, no raw `slice/sliceMax` remains, `sliceFraction(0,0)=0`. (The `realSampler` NaN-bounds gap is now unreachable — the crosshair z is the only NaN source and it's guarded at the origin — so left as-is rather than adding a defensive patch.)
  - **Overlooked sibling, fixed later same day:** `dataset.ts::voxelToWorld` divided by `(dim-1)` — the *same* single-slice `/0` hazard, reachable via `sliceWorldZ` (CT panel + 3D slice planes), left unguarded. So the single-slice safety was half-done. Applied the matching `Math.max(1, dim-1)` guard (identical for all real data — dim ≫ 1 — verified; NaN→-ext at dim=1). The coordinate normalization is now uniformly single-slice-safe. Other `/(N-1)` sites (`mpr.ts`) use render dimensions that are never ≤1.
  - **Refs:** `src/store.ts:7`, `src/components/Viewer3D.tsx:171`, `src/components/MPRStrip.tsx:70,85,140`.

- [x] **Orientation audit reported canonicalized data as "raw". — FIXED 2026-07-09.** `geometry.py::_audit` read CT/MR via `load_ct_mr`, which this session made canonicalize-on-read — so the "raw orientations" line always printed `LPS` and the follow-up `canonicalize()` was a no-op. Fed a flipped/oblique acquisition, the audit would have hidden the reorientation, defeating its diagnostic purpose (invisible today only because all current data is already LPS).
  - **Root cause:** canonicalization was added to `load_ct_mr` (correct for the pipeline) *after* the audit was written to read through it; the audit is the one caller that needs the *raw* orientation. Sweep confirmed the other three callers (`prepare_output_volumes`, `register_ct_mr` QA, `medsam2.load_inputs`) all want canonicalized, so this was the sole mismatch.
  - **Resolution:** `load_ct_mr(case_dir, to_lps=True)` — the audit passes `to_lps=False` to get true raw (no file-discovery duplication). Verified on a synthetic flipped case: raw now reports `RPI`, then `canonicalize → LPS`; the real LPS case is unchanged.
  - **Refs:** `scripts/register_ct_mr.py:42`, `scripts/geometry.py:142`.

## P3 — Stale-artifact review (2026-07-09)

- [x] **Stale ML approach lingering in a doc. — FIXED 2026-07-09.** `ct-mri-tumour-segmentation.md` contradicted itself: §4.5 step 4 documents that uncertainty-from-agreement was **tested and failed** (AUROC≈0.50 → recall-safe pivot), but 6 later passages (lines 245/249/291/351/371/381) still sold the abandoned uncertainty map as the live confidence mechanism. **Root cause:** the pivot updated §4.5 but the downstream sweep was incomplete. All 6 now describe the recall-safe envelope + coverage/coherence routing. Also added a "uncertainty later failed → see triage §4" pointer to `medsam2-seed-test-plan.md §8`, and repointed `head-and-neck-segmentation.md`'s asset-format refs from the (stale) README block to `schema.md`.

- [ ] **Local disk artifacts (gitignored, not in repo) — user's call to reclaim ~5.8G:** `hanseg_data/HaN-Seg.zip` (4.6G, redundant after extraction to `HaN-Seg/`), `runs/sweep` (1.2G, orphaned MedSAM2 prompt/modality sweeps — no committed script produces it), `runs/medsam2_seed` (812M) + `runs/triage` (405M) experiment outputs. Not deleted here (large, user-created data). `hanseg_data/registration_check/mr_in_ct.nrrd` (~847M) is a regenerable intermediate.

- [x] **Removed the abandoned-uncertainty ML code; preserved the findings. — DONE 2026-07-09 (user-directed).** The uncertainty-from-agreement approach was disproven (AUROC≈0.50) and pivoted away from, but its code lingered. Removed `calibration_experiment.py` (the falsification test) and the `--uncertainty`/`--jitter` experiment-D machinery from `medsam2_seed_test.py` (the per-voxel `uncertainty.nrrd` generator, unused by production). **Root cause:** the pivot updated the production path + some docs but left the experiment apparatus behind. The full experiment **design + findings are now self-contained in `tumour-triage-pipeline.md` §4/§8a** so it is not re-attempted; doc refs in design/schema/medsam2-setup updated. `segment`'s `shift`/`rng` (used by triage's seed ensemble) kept.

## 3D rendering pipeline review (2026-07-09)

- [x] **`CutPlane` rebuilt its slice texture even when hidden. — FIXED.** `Viewer3D.tsx` `CutPlane` gated `showCutPlane` only on the render (`return null`), not on its `useMemo` — so scrubbing/windowing/timepoint/fusion changes ran `makeRealSliceTexture` (full slice render + GPU `CanvasTexture` upload) and discarded it whenever the cut plane was toggled off. **Root cause:** the visibility flag gated output, not the resource build. Its siblings do it right — `LayerStack` checks `showLayers` inside the memo, `MPRBox` gates by not mounting `MPRQuad` (so `renderReformat` never runs). Fixed by gating the memo on `showCutPlane` too (added to the condition + deps). Not a leak (memoized+disposed), a wasted-work/inconsistency fix. `tsc` + `vite build` green.
- [x] **Duplication: the "effective display mode" fallback. — FIXED.** `mri ? displayModality : "ct"` (use the chosen modality, fall back to CT when a timepoint has no MR) was copy-pasted across **6 sites in 3 files** (`Viewer3D` ×3, `MPRStrip` ×2, `CTPanel`). Extracted `dataset.ts::effectiveMode(mri, mode)` — one rule, same class as `sliceFraction`/`window_u8`. Behavior-preserving; full build green.

## MPR / reslicing feature review (2026-07-09)

Deep read of `mpr.ts` + `MPRStrip.tsx`. **No bugs** — verified the click→crosshair round-trip (coronal `crossUV.x` reduces exactly to `crossX`, vertical to `1 − slice/sliceMax`; the click handlers invert it consistently), the wheel/oblique-modulo wrap, and the MIP overlay. Two genuine duplications fixed:

- [x] **`mpr.ts`: aspect→canvas-size math duplicated** in `renderReformat` + `renderMIP` (identical 3 lines) → extracted `fitBox(aspect, base)`.
- [x] **`MPRStrip.tsx`: the tile shell duplicated** — `ProjectionTile` and `MIPTile` each re-implemented the canvas-write `useEffect`, the crosshair-overlay JSX (3 divs), the `proj-tile`/`proj-stage` structure, and the click fx/fy math. **Root cause:** no shared projection-tile component. Extracted `ProjCanvas` (takes `img`/`aspect`/`cu`/`cv`/`label`/`depth` + `onPick(fx,fy)`/`onWheel(deltaY)`); the two tiles are now thin wrappers differing only in their `useMemo` and handlers. 169→149 lines; `putImageData` and the overlay now single-source. `tsc` + `vite build` green (visual/interactive check would need the browser e2e).

## 2D slice-rendering feature review (2026-07-09)

Deep read of the pixel pipeline (`dataset.ts`: `renderRealSlice`/`srcLum01`/`windowLum`/`shade`/`tintPixel`/`labelColor`) + its consumer `CTPanel.tsx`. **Correct** — verified the storage-index math + Y-flip in `renderRealSlice`, the `windowLum` div-by-zero guard, and the crosshair round-trip in CTPanel (`click (fx,fy) → {crossX:fx, crossY:1−fy} → displayed at (fx,fy)`), plus that `crossX`/`crossY` are consistent world-fractions across CTPanel and every MPR tile (`crossY=1` ↦ max-y in each view's axis). The shading path (`srcLum01`→`windowLum`→`shade`) is already shared with the MPR sampler. No bugs.

- [x] **Duplicated `clamp01` primitive. — FIXED.** Clamp-to-[0,1] was a *named* function in `MPRStrip` but re-inlined as `Math.max(0, Math.min(1, …))` in `CTPanel` (×2) and `mpr.ts` `crossUV` (×6). Hoisted `clamp01` to `dataset.ts` (next to `mix`/`windowLum`); all sites route through it. Same class as `sliceFraction`/`effectiveMode`/`fitBox`. tsc + vite build green.

## Store (`store.ts`) feature review (2026-07-09)

Deep read of the state hub. **Correct** — `setSlice`/`setTimepoint` clamp; `setWindow`/`setLevel` don't but inputs are bounded; `loadDataset` guards concurrent loads (no stale-response race) and clears `loading` on both success and error (can't get stuck); `setDisplayMode`'s `m === "mri" ? mriWL ?? defaultWL : defaultWL` parses correctly (`??` > `?:`) and `effectiveMode` + the `loadDataset` reset keep `displayModality` safe. No bugs.

- [x] **Clamp-primitive duplication consolidated. — FIXED.** `setSlice`/`setTimepoint` inlined `Math.max(0, Math.min(N, v))` — the `[0,N]` sibling of the just-added `clamp01`. Root cause: extracted `clamp01` but no *general* clamp, so index clamps stayed inlined. Made `dataset.ts::clamp(v, lo, hi)` the single primitive; `clamp01` derives from it; `store.ts` `setSlice`/`setTimepoint` use it. **Sibling caught in the same sweep:** `windowLum` (same file) inlined a `[0,255]` clamp → now uses `clamp` too. No inline two-sided clamps remain in `src`. tsc + vite build green.

## Python registration internals review (2026-07-09)

Deep read of `register_ct_mr.py`. **Well-engineered and correct** — verified: the never-regress guard scores seed/rigid/affine with one consistent fixed-seed metric so they compare directly; `seed` is copied from `rigid0` *before* Stage 1 mutates it in place (correct coarse-only fallback); the transform is FIXED→MOVING in physical space so it stays valid across LPS canonicalization; `mandible_qa`'s `zc` from the `(z,y,x)` argmax indexes the sitk z-slice correctly. No bugs.

- [x] **Display-flip duplicated 7× across two files. — FIXED.** `sitk.Flip(img, [False, True])` (the "radiological +Y up" QA flip) was inlined 4× in `register_ct_mr` and 3× in `medsam2_seed_test`'s `write_qa`. Extracted `register_ct_mr._flip_y`; medsam2 imports it (as it already imports the sibling `_mr_slice_u8`). **The medsam2 sites were caught by a cross-file sweep**, not the first pass. Behavior-identical; py_compile + imports green.

## MedSAM2 segment/propagate internals review (2026-07-09)

Deep read of `medsam2_seed_test.py`'s engine (`prepare_case`/`segment`/`propagate`/`roi_crop`/`pick_seed_slice`). **Correct** — verified: `segment` maps `seed_full` into cropped coords via `seed_full - (cz.start or 0)` (handles `no_crop`'s `slice(None)`), and the seed is always inside the crop (it bounds the full GT bbox); `propagate` applies the seed prompt → forward, **resets + re-applies** → reverse, so the seed frame is written by both directions with the same prompt (no conflict) and the union covers all frames; the ROI paste-back shapes match; `dice`/`surface_metrics` guard empty masks. No bugs.

- [x] **Largest-connected-component logic duplicated across 3 files. — FIXED.** `measure.label` + argmax-of-component-sizes appeared in `medsam2.largest_cc`, `build_envelope.body_mask` (inline), and `preprocess_hn.propagate_intensity` (a `sizes[0]=0; argmax` variant — same result). **A cross-file sweep caught the latter two**, not the first pass. Extracted `asset_common.largest_cc` (pure numpy/skimage, reuses its existing `measure` import); all three now share it, and `build_envelope`'s now-dead `from skimage import measure` was removed. Verified byte-identical envelope rebuild (body mesh + seg volume); medsam2/preprocess_hn are behavior-identical by construction. `triage.largest_cc_fraction` stays (it needs the counts for a *fraction*, not the mask).

## Triage pipeline internals review (2026-07-09)

Deep read of `triage_pipeline.py`. **Correct** — verified `recall_safe_envelopes` (EDT of `~consensus` thresholded `<= r` = spacing-aware dilation; the bbox restriction with a `ceil(rmax/spacing)+1` margin fully contains the dilation, so it loses nothing), the air-prune precedence + grid-shape alignment (`ct_vol` is on the envelope's grid, available even in `mr` modality), the majority vote, and `recall_precision`'s empty guards.

- [x] **`slice_areas` duplicated. — FIXED.** The per-axial-slice area `mask.reshape(mask.shape[0], -1).sum(1)` was inlined in `medsam2.pick_seed_slice` and `triage.main`. Extracted `medsam2_seed_test.slice_areas`; both share it. py_compile green.

- [ ] **(P3, design nuance) `coherence` is measured on the *dilated envelope*, not the consensus.** The fragmentation signal (`largest_cc_fraction`, doc §5 "propagation broke up") runs on the `+dilate-mm` envelope, where dilation has already merged fragments closer than ~2·radius — so it under-detects the small fragmentation the *consensus* would show. **Not changed:** it alters the measured pipeline's routing and needs GPU re-measurement on real data. Consider measuring coherence on the pre-dilation `consensus` for a signal that matches the stated intent.
  - **Refs:** `scripts/triage_pipeline.py:119`.

## Envelope builder layer-assembly review (2026-07-09)

Deep read of `build_envelope_dataset.py`. **Correct** — paint precedence (list order body→bone→organ→tumour, later wins → `seg` = last containing layer), per-layer meshes from each full mask (independent of 2D precedence), `bone = (ct_hu>200) & body` (excludes CT table), and `_to_grid(a.tumour)` evaluated once. No bugs.

- [x] **`mesh_from_mask` crashed on an empty mask; `build_envelope` didn't guard it. — FIXED.** `marching_cubes(level=0.5)` raises "level must be within data range" on an all-empty volume. `preprocess_hn_mri`/`preprocess_hn` guard `mask.sum()==0` *before* meshing, but `build_envelope` meshes every layer with no check — so a misaligned/empty organ or tumour layer (which `warn_if_no_overlap` only *warns* about) would crash with a cryptic skimage error. **Root cause:** the empty case was left to each caller instead of the shared helper. Guarded it in `asset_common.mesh_from_mask` (empty mask → empty mesh) — all callers now safe; `preprocess_hn_mri`/`hn` still `raise` first on empty (unchanged). Verified: empty→empty mesh no crash, non-empty envelope rebuild byte-identical; frontend renders an empty mesh as nothing.

## Done in this review (2026-07-09)

Doc inaccuracies found by adversarial verification against the code and already corrected:

- [x] **`hasSegmentation` mislabeled as "never read."** It *is* read from the manifest (`App.tsx:40`, `ControlRail.tsx:70`) to gate segmentation UI. Fixed in `schema.md §2/§10` and `design.md §8.3`.
- [x] **`bboxMm` reorder cite** corrected `preprocess_hn_mri.py:122` → `:127` (`schema.md §9`).
- [x] **Palette fallback** softened `% 5` → `% LABEL_PALETTE.length` (`schema.md §6`).
- [x] Verified (CONFIRMED) the remaining ~17 load-bearing claims: gzip-6, `idx = x + X·(y + Y·z)`, uint8, dead `storageWindowHU`/`meshes`, no runtime validation, label convention, palette/colors, thresholds & paint precedence, grid dims, coordinate axes, MIP-along-Y, registration cache path, `metrics.json` write, the full import/dedup graph, and all CLI defaults.
