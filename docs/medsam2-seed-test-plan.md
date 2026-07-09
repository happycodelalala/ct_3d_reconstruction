# MedSAM2 Zero-Shot Seed Test — Rationale & Plan

A concrete, self-contained experiment to validate the [§4.5 segmentation recipe](ct-mri-tumour-segmentation.md#45-in-our-setting-sparse-seeds-no-labels-gpu-server)
on **real 3D ground truth**, before we trust it on tumours (which we have no labels for).
Runs on the **3080m laptop (16 GB, CUDA, Manjaro)**. API details are deliberately deferred —
we validate/debug them live on the laptop; this doc is the rationale + plan only.

---

## 1. Rationale — why this test, and why the mandible

**The problem:** the §4.5 plan (single seed slice → MedSAM2 propagation on MR → 3D mask) is
web-verified in principle, but we've never run it on our own data, and we have **no tumour
labels** to measure it against. So we can't tell whether the *mechanism* works here versus
whether we've mis-wired the prompt/propagation.

**The trick:** HaN-Seg gives us, for `case_01`, everything we need to test the mechanism with
a real 3D label — **no tumour, no manual labelling, no training**:
- CT and MR **already registered** into one grid (`register_ct_mr.py`), and
- a **full 3D mandible mask** (an OAR) as ground truth.

So we treat the **mandible as a stand-in target**: take **one** mandible slice as the "seed",
propagate it through the MR with MedSAM2, and score **Dice against the full mandible mask**.
That exercises the exact recipe — mask prompt, center-outward propagation, drift behaviour,
uncertainty — end to end, on real CT+MR data, with a number we can trust.

**What it does and does not tell us (honest framing):**
- ✅ It validates the **mechanism**: does a one-slice prompt propagate into a correct 3D mask
  on *our* registered volumes, with *our* code path?
- ⚠️ The mandible is a **well-defined, high-contrast, single connected bone** — the *easy* case
  for SAM2 propagation. A real H&N tumour (soft, iso-intense-ish, irregular, sometimes
  multifocal) will be **harder**. So a good mandible Dice is an **upper-bound sanity check**,
  not a tumour-accuracy estimate. A *bad* mandible Dice, though, is decisive — if the
  mechanism can't recover a clean bone from one slice, it won't do tumours, and we rethink
  before investing further.

---

## 2. Environment plan (laptop — Manjaro, CUDA)

- **Box:** 3080m, 16 GB — comfortable; MedSAM2 runs slice-wise so VRAM is not the constraint.
- **Env:** `uv venv` + the **CUDA** PyTorch wheel + MedSAM2 from its repo + checkpoint. Exact
  commands finalized **live** (pinned versions, wheel index, checkpoint URL) — see §6 open items.
- **Later (phase 2):** re-run on the **W7900 (48 GB, ROCm)** for cohort batch; expect a ROCm
  torch wheel + `HSA_OVERRIDE_GFX_VERSION=11.0.0` and a flash-attention → eager fallback. Not
  in scope for this first test.

## 3. Data plan (after a fresh clone)

`hanseg_data/` and `public/data/` are gitignored, so a clone won't have them. Two routes:
- **Full:** download HaN-Seg (4.6 GB) → extract `set_1/case_01` → the test script registers
  inline (reuses `register()` from `register_ct_mr.py`). One command, ~5 min for registration.
- **Light:** copy just two NRRDs from this dev box — `hanseg_data/HaN-Seg/set_1/case_01/
  case_01_IMG_CT.nrrd`, `..._IMG_MR_T1.nrrd`, `..._OAR_Bone_Mandible.seg.nrrd` (or the
  already-registered `registration_check/case_01/mr_in_ct.nrrd` + the mandible) — then skip
  re-downloading.

The harness will accept **`--case-dir`** (raw HaN-Seg case → register inline) *or* **`--mr` +
`--mask`** (already-registered fast path), so either route works.

## 4. Test design — what `scripts/medsam2_seed_test.py` will do

Reuses `register()` / `load_ct_mr()` from `register_ct_mr.py`; new code is only the MedSAM2 call.

1. **Load & register** (or take pre-registered MR): CT, MR, mandible mask all on one grid.
2. **Choose the seed slice.** Default = the mandible's **largest-area axial slice** (the
   center-outward anchor the literature says is most stable). A `--seed-slice` override lets us
   deliberately test an **off-center** seed to observe drift.
3. **Prompt & propagate (MedSAM2).** Feed the seed slice's mandible mask as a **mask prompt**
   (not just a box — precedent 0.71 vs 0.57), then propagate **bidirectionally** (forward +
   backward) through the MR volume → predicted 3D mask.
4. **Score.** Volumetric **Dice** vs the full mandible mask; optionally surface-Dice / HD95.
5. **Uncertainty (optional flag).** Repeat N times with **jittered prompts** (shift/scale the
   box, ± augmentations); STAPLE/vote → consensus mask + **per-voxel uncertainty map**.
6. **CT constraint (optional flag).** Nothing to reject for the mandible (it *is* bone), but
   wire the hook so the tumour path can exclude bone/air later.
7. **Outputs:** predicted mask NRRD, a QA PNG (pred vs GT overlay on mid slices, like the
   registration QA), and printed metrics.

**Planned experiments (cheap, one case):**
| Run | Prompt | Purpose |
|---|---|---|
| A | mask, center slice | best-case ceiling of the mechanism |
| B | box, center slice | quantify the mask-vs-box gap (expect ~0.71 vs 0.57) |
| C | mask, **off-center** slice | does it drift? validates the center-outward argument |
| D | A + multi-prompt | sanity-check the uncertainty map vs where errors actually are |

## 5. Success criteria

- **Mechanism works** if run **A** recovers the mandible at **high Dice** (≳0.85 is reasonable
  for a clean bone from one slice) with coherent propagation.
- **Center-outward matters** if **C ≪ A** (off-center drifts), confirming step 3 of the recipe.
- **Uncertainty is useful** if **D**'s high-uncertainty voxels coincide with the actual errors.
- **Red flag:** if even **A** is poor / drifts badly, the promptable-propagation approach is
  weaker than the literature suggested on our data — we stop and reconsider (classical
  multimodal random-walker, or a different model) before touching tumours.

Record the actual numbers back into [§4.5](ct-mri-tumour-segmentation.md) so the plan on record
reflects measured reality, not just literature.

## 6. Open items to validate **live on the laptop** (don't guess these)

> **RESOLVED** — install + API are now known and captured as a runbook:
> [docs/medsam2-setup.md](medsam2-setup.md). Notably: entry point
> `build_sam2_video_predictor_npz` (inits state from a numpy/tensor volume directly),
> the model is **512²** (not 1024²), no `pip install -e` needed, and the seed prompt uses
> `add_new_mask`. Original open list kept below for the record.

The MedSAM2 API is the part we finalize when we can run it:
- exact **install** (package name, pinned versions, CUDA wheel index) and **checkpoint/config**;
- 3D/video predictor entry point (`build_sam2_video_predictor`?) and how to **init state from a
  numpy volume** vs a frames directory;
- **`add_new_mask`** vs `add_new_points_or_box` signatures for the seed prompt;
- **`propagate_in_video`** forward + backward usage;
- **frame preprocessing**: SAM2 expects ~1024², 3-channel — replicate the grayscale MR to RGB,
  and settle the intensity normalization (reuse the MR robust-percentile window from the pipeline).

## 7. Sequence on the laptop

1. `git clone`, `uv venv`, install CUDA torch + MedSAM2 + checkpoint (§6).
2. Get `case_01` data (§3, light route is fine).
3. Smoke test: MedSAM2 loads + segments one 2D slice from a box (confirms the install/API).
4. Write/finish `medsam2_seed_test.py` against the now-known API; run **A** → first Dice.
5. Runs **B/C/D**; save QA PNGs + numbers.
6. Fold measured results into [§4.5](ct-mri-tumour-segmentation.md); decide go/no-go for the
   tumour path.

## 8. Results (measured 2026-07-04, RTX 3080, `case_01`)

> **Note on experiment D (uncertainty):** the per-voxel uncertainty map (§4/§5) was
> subsequently tested for calibration and **failed** (AUROC ≈ 0.50 at predicting error) —
> confidence pivoted to a recall-safe envelope. See [tumour-triage-pipeline.md §4](tumour-triage-pipeline.md).
> The Dice results below are the mechanism's segmentation accuracy, independent of that.

Ran on the laptop as planned. Environment resolved the §6 unknowns: entry point
`build_sam2_video_predictor_npz`, init from a numpy/tensor volume, **512²** (not 1024²),
mask prompt via `add_new_mask`, bidirectional `propagate_in_video` with `reset_state` between,
ImageNet-normalized RGB-replicated input. Harness: [`scripts/medsam2_seed_test.py`](../scripts/medsam2_seed_test.py).

| Run | Modality | Prompt | ROI margin | Dice | pred/GT vol |
|---|---|---|---|---|---|
| **A′** | **CT** | **mask** | **6 mm** | **0.89** | **1.02** |
| A | CT | mask | 12 mm | 0.67 | 1.65 |
| — | CT | mask | uncropped | 0.20 | 6.9 |
| B | CT | box | 12 mm | 0.67 | 0.58 |
| — | MR | mask | 6 mm | 0.46 | 2.0 |
| — | MR | box | 12 mm | 0.27 | 1.6 |

**Verdict — GO, with two corrections to the plan:**
- ✅ **Mechanism validated** (§5 success criterion met): CT + mask + tight ROI → **Dice 0.89**,
  volume-matched. Single-slice → 3D propagation genuinely works on our data.
- ⚠️ **Plan miss #1 — cropping is mandatory.** Uncropped, propagation over-segments ~7× and
  drifts into skull/facial bone (Dice 0.20). A **tight ROI crop** is a first-order recipe step,
  now added to [§4.5 step 3](ct-mri-tumour-segmentation.md#45-in-our-setting-sparse-seeds-no-labels-gpu-server).
  This is the drift the plan anticipated (exp C), but it dominates even from a *centre* seed.
- ⚠️ **Plan miss #2 — the mandible is not an MR proxy.** §1 called it "high-contrast"; that's
  true on **CT**, but on **T1 MR cortical bone is a signal void**, so the mandible caps at
  ~0.46 on the very modality the recipe uses. It's a valid upper-bound check for a *CT-visible*
  target; the fair **MR** test is a soft-tissue OAR (parotid/brainstem), which is what a tumour
  actually resembles. → **next experiment.**

**Inference profile:** ~15–27 ms/slice, ≤2.2 GB VRAM, ~2–4 s per volume. Bottleneck is the
CT↔MR registration (~5 min CPU), now cached to `mr_in_ct.nrrd`. Cohort = registration-bound.

---

*Recipe under test: [`docs/ct-mri-tumour-segmentation.md` §4.5](ct-mri-tumour-segmentation.md#45-in-our-setting-sparse-seeds-no-labels-gpu-server).
Reused code: [`scripts/register_ct_mr.py`](../scripts/register_ct_mr.py).*
