# Tumour triage pipeline — CT + MR, sparse/noisy seeds, no reliable labels

A design for **automatic-assisted tumour segmentation** under our real constraints, targeting
**triage** (a high-recall rough 3D envelope + confidence a human confirms), not measurement-grade
contours. It builds on the validated single-seed → 3D-propagation recipe
([§4.5](ct-mri-tumour-segmentation.md#45-in-our-setting-sparse-seeds-no-labels-gpu-server),
[seed-test results](medsam2-seed-test-plan.md#8-results-measured-2026-07-04-rtx-3080-case_01)).

**Read this first:** the plan is split into a **minimal validated core (§5)** we build and measure
now, and **evidence-gated extensions (§9)** we build *only* once the core earns it. Everything in
the core is measurable today on OAR proxies; everything in §9 is deferred on purpose. Keeping that
line sharp is the whole point — it's what stops this from becoming a system we can't validate.

---

## 1. The setting (what's real)

- **CT + MR only** — no PET, no DWI/ADC, no post-contrast. (Those would change automatic
  *detection*; we don't have them.)
- **Annotations are semi-reliable** — scattered across slices, sometimes **contradicting**, and
  **absent** on some cases.
- **No dense, reliable tumour labels** → we can't train supervised, and we can't directly measure
  tumour Dice. Both facts shape every choice below.
- **Goal: triage.** High **recall** (a missed tumour is the real failure; a false positive is a
  quick human dismissal), a rough 3D envelope, a per-voxel confidence, a review queue — **not** a
  measurement-grade contour.

## 2. Design stance

1. **Recall over precision.** Never silently delete a candidate unless deletion is *provably* safe
   (§6); otherwise downweight and flag.
2. **Contradictions become uncertainty, not fiat.** Propagate each hypothesis; let agreement /
   disagreement produce the confidence map. The human adjudicates genuine conflicts.
3. **Route by confidence.** Spend the reviewer's time on the risky cases; auto-accept the obvious
   ones (pending a glance).
4. **Human-in-the-loop is the design, not a fallback.** You cannot certify a label-free
   autosegmenter; you can certify a fast reviewer with good triage.
5. **Everything downstream of a seed is cheap** (~3 s propagation, registration cached), so we can
   afford many hypotheses per case.

## 3. Two problems, very different difficulty

| | Status |
|---|---|
| **Delineate** a boundary given a rough location | **Working** — MedSAM2 seed → propagate, Dice **0.73 (soft tissue, MR) – 0.89 (bone, CT)** |
| **Detect** *where* the tumour is, with no/bad seed | **Hard, unsolved** — deferred to §9; SAM-style models don't find, they delineate |

The core (§5) assumes **≥1 (noisy) seed per lesion** and reuses the validated propagation. Detection
with no seed is a separate, later track.

## 4. The load-bearing assumption — TESTED, and it FAILED (2026-07-04)

The original premise was:

> Consensus + per-voxel uncertainty, computed from **noisy** seeds, is calibrated — high
> uncertainty predicts where the mask is wrong.

We tested it first (`scripts/calibration_experiment.py`, brainstem/mandible proxies with simulated
noisy seeds) and it **fails**: **AUROC ≈ 0.50** (uncertainty is no better than chance at ranking
error voxels), and **~22 % of missed tumour is a "silent miss"** — invisible to the uncertainty map.
It fails identically with 0 % gross-contradiction seeds, so it's intrinsic, not seed-noise.

**Root cause:** agreement-based uncertainty measures **variance** across seeds, but MedSAM2's
dominant error is **bias** — it systematically *under-segments*. Every seed under-shoots the same
boundary, so they **agree** exactly where the mask is wrong; variance is blind to bias. (Voting
reduces variance but not shared bias, so consensus can't beat the best single noisy seed either.)

**The pivot (validated):** since the failure is under-segmentation and triage's danger is *missed*
tumour, replace variance-uncertainty with a **recall-safe envelope** — dilate the good delineation
outward (spacing-aware) to cover the systematic miss, and let the human tighten. Under good seeds a
**+2 mm** envelope lifts recall 0.70→0.88 (brainstem) / 0.90→0.98 (mandible) and even improves Dice;
**+4 mm** reaches ≥0.97 recall for a recall-first operating point (§8a). This targets the actual
failure head-on. Confidence is now **coverage-based** (how much dilation to reach a given recall),
not seed-agreement.

> **Lesson banked:** the go/no-go gate cost one experiment and one refactor, not a whole pipeline —
> which is exactly why it went first.

## 5. The MVP core

```
CT/MR + seeds
   │
[G] registration MI gate (case-level) ─── fail ──► "registration failed → manual"
   │ pass
[A] ensemble propagate: each seed + jitter ──► N candidate masks
   │
[B] consensus (vote/STAPLE) ──► recall-safe envelope: dilate +Nmm to cover under-seg (§4)
   │
[C] body-mask HARD gate + air prune  (safe deletes only, §6)
   │
[D] confidence = coverage (dilation to reach target recall); seed agreement a weak secondary
   │
[E] route: {auto-accept (glance) | REVIEW (with reason)}  ──► human confirm in workstation
                                                                  │
                                              correction = clean seed ↺ re-propagate (seconds)
```

- **[G] Registration gate.** `register()` already returns the Mattes-MI; a case whose MI is poor is
  flagged **"registration failed → manual"** and never propagated into noise. This is the one
  *added* safeguard — real data will have registration failures the pipeline must not trust
  silently.
- **[A] Ensemble propagate.** Take the seeds as they are (assume one lesion), plus jittered copies
  (`--uncertainty`, scaled), each a tight-ROI bidirectional MedSAM2 run on **MR** (tumours are
  soft-tissue; CT is used only for the gate in [C]). Tight ROI is the first-order drift control —
  Dice collapsed 0.89 → 0.67 → 0.20 as the ROI loosened.
- **[B] Consensus → recall-safe envelope.** Vote/STAPLE the N masks → one mask, then **dilate it
  +N mm** (spacing-aware) to cover the model's systematic under-segmentation (§4). Per-voxel
  seed-agreement uncertainty is **not** used for confidence — it's miscalibrated (§4).
- **[C] Body gate + air prune.** Delete only what is *provably* not tumour: voxels **outside the
  body mask** (the exact failure we saw uncropped) and voxels at **air HU**. Both are safe hard
  deletes (§6). Nothing else is deleted in the core. Apply *after* the dilation, so the envelope
  can't leak out of the body.
- **[D]/[E] Confidence + routing.** Confidence is **coverage-based** — how much dilation was needed
  to reach the target recall, plus propagation coherence. Incoherent propagation or seeds that don't
  converge → **REVIEW** with the reason. **Non-convergence of the seeds is itself the signal** —
  we do not need to cluster or classify contradictions to be safe (§7). Rank the queue by confidence
  so risky cases surface first.

## 6. Hard gates vs soft priors (the safety split)

The original draft conflated these; they have opposite safety profiles.

- **HARD (delete / reject) — only when provably safe for a tumour:**
  - registration failed (case-level, §5 [G]);
  - **outside the body** (a tumour is never outside the patient) — body mask = CT `HU > −500`,
    largest connected component, holes filled;
  - **air HU** inside a soft-tissue mask.
- **SOFT (downweight / flag, never delete) — everything anatomical:** overlap with a normal-organ
  prediction, bone HU (tumour can invade/abut bone; partial-volume voxels are real). These live in
  §9, not the core — a hard organ mask would cost recall exactly where a tumour replaces an organ.

## 7. Noisy / contradicting seeds — the lean handling

- **Noisy seed** → weak prompt: jitter, propagate, consensus. Perturbation-stable regions are
  trustworthy.
- **Contradicting seeds** → propagate each independently and let [B] express the disagreement as
  uncertainty. **Convergence = confident core; non-convergence = REVIEW.** We do **not** cluster,
  type, or adjudicate contradictions in the core — non-convergence routing is already safe, and
  clustering/multifocal handling is an §9 extension gated on evidence of multi-lesion data.

## 8. Validation without tumour labels (do this first)

Measured on OAR proxies (mandible / brainstem / parotid — GT known), with **simulated** noisy +
contradicting + wrong-slice seeds:

1. **Does consensus beat the best single noisy seed's Dice?** (is the ensemble worth it)
2. **Does high uncertainty overlap the actual error region?** — the **load-bearing calibration
   test** (§4). If this fails, stop and rethink before building further.
3. **Does the body gate suppress false positives with recall ~flat?**

Plus test–retest consistency (jittered seeds, same case) and, in use, the **human override rate** as
the real-world quality metric.

## 8a. Measured results (2026-07-04, `case_01`)

`scripts/calibration_experiment.py` on the brainstem (MR) and mandible (CT) proxies.

**Calibration (the load-bearing test) — FAILED** (see §4 for the root cause): AUROC ≈ 0.50 for
uncertainty-predicts-error, ~22–24 % silent misses, and consensus does not beat the best single
*noisy* seed. → variance-uncertainty dropped as the confidence mechanism.

**Recall-safe envelope (the pivot) — WORKS**, under good-quality seeds:

| target (good seeds) | raw recall / prec | +2 mm | +4 mm |
|---|---|---|---|
| Brainstem (MR) | 0.70 / 0.91 | **0.88 / 0.78** (Dice 0.83) | 0.97 / 0.56 |
| Mandible (CT) | 0.90 / 0.85 | **0.98 / 0.61** | 0.99 / 0.40 |

A **+2 mm** dilation is a reasonable default (recovers most misses, Dice flat-to-up); push to +4 mm
for a recall-first operating point. Precision falls faster on thin structures (mandible) than compact
ones (brainstem), so the radius is a per-target knob the human's confirm step absorbs.

**Consequence for the plan:** the pipeline is finishable **starting from good-quality annotations**
(the pragmatic starting condition), with a recall-safe envelope + human confirm. Noisier-annotation
robustness is deferred until the good-conditions loop is solid.

## 9. Evidence-gated extensions (deferred on purpose)

Each stays out of the build until its gate is met.

| Extension | Build only once… |
|---|---|
| **Organ map + soft anatomical prior** (downweight normal-organ overlap; TotalSegmentator / OARs) | the core's FP rate is measured and organ context is shown to reduce it without hurting recall |
| **Seed clustering / multifocal / contradiction typing** | the data actually contains multi-lesion or nodal cases the non-convergence signal handles poorly |
| **Modality auto-select + CT/MR fusion** | a measured case shows MR-only misses a (bone-involved) tumour a fusion would catch; fusion rule chosen by measurement |
| **Seedless detection** (normal-anatomy subtraction, paired-organ asymmetry) | the seeded path is solid and seedless cases are common enough to justify the least-reliable component |
| **Anatomical location reporting** ("left parotid mass, abuts mandible") | reviewers ask for it; needs the organ map first |

## 10. Cost

Registration ~5 min CPU, **cached once per case** (`transform.tfm`). Propagation
**~15–27 ms/slice, ≤2.2 GB VRAM**, ~2–4 s/volume; the ensemble is a small multiple of that. Body
mask is trivial. **The cohort is registration-bound, not GPU-bound.**

## 11. What exists vs what's new — build order (risk-first)

**Exists:** cached MR→CT registration (with MI); MedSAM2 seed→propagate harness with tight-ROI
crop, `--uncertainty` (jittered consensus + uncertainty map), surface metrics; OAR masks; the
CT/MR/fusion workstation.

**Build order (each gates the next):**
1. **Calibration experiment (§8)** on proxies with simulated bad seeds — validate §4's assumption
   *first*. Mostly uses what exists (`--uncertainty`) + a noisy-seed simulator + an error-vs-
   uncertainty overlap metric.
2. **Registration MI gate** [G] and **body gate + air prune** [C] — cheap, safe, measurable FP drop.
3. **Multi-*real*-seed ensemble + confidence routing** [A/D/E].
4. Only then, §9 extensions as their gates are met.

## 12. What this is *not*

Not a fire-and-forget autosegmenter, and not measurement-grade. Automatic **detection** stays
unreliable on CT+MR alone (PET would change that). The design **depends on a human** confirming
flagged cases. What it delivers is a **high-recall, uncertainty-aware triage** that turns scattered
unreliable annotations into a fast, honest review loop — and it only earns each new capability by
measuring the last one.

---

*Related: [CT+MR tumour segmentation](ct-mri-tumour-segmentation.md) ·
[MedSAM2 seed-test plan & results](medsam2-seed-test-plan.md) ·
[MedSAM2 setup & usage](medsam2-setup.md).*
