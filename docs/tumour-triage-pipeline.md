# Tumour triage pipeline — CT + MR, sparse/noisy seeds, no reliable labels

A design for **automatic-assisted tumour segmentation** under our real constraints, targeting
**triage** (a high-recall rough 3D envelope + confidence a human confirms), not measurement-grade
contours. It builds on the validated single-seed → 3D-propagation recipe
([§4.5](ct-mri-tumour-segmentation.md#45-in-our-setting-sparse-seeds-no-labels-gpu-server),
[seed-test results](medsam2-seed-test-plan.md#8-results-measured-2026-07-04-rtx-3080-case_01))
and adds a **normal-anatomy envelope** to control false positives and enable label-free detection.

---

## 1. The setting (what's real)

- **CT + MR only** — no PET, no DWI/ADC, no post-contrast. (These would change everything for
  automatic *detection*; we don't have them.)
- **Annotations are semi-reliable** — scattered across slices, sometimes **contradicting each
  other**, and **absent** on some cases.
- **No dense, reliable tumour labels** → we can't train supervised, and we can't directly measure
  Dice on tumours. Both facts shape every choice below.
- **Goal: triage.** High **recall** (a missed tumour is the real failure; a false positive is a
  human's quick dismissal), a rough 3D envelope, a per-voxel confidence, and a review queue —
  **not** a measurement-grade contour.

## 2. Design stance

1. **Recall over precision.** Never silently delete a candidate; downweight and flag instead.
2. **Contradictions become uncertainty, not fiat.** Propagate each hypothesis; let agreement /
   disagreement produce the confidence map. The human adjudicates genuine conflicts.
3. **Route by confidence.** Spend the reviewer's time on the risky cases; auto-accept the obvious
   ones (pending a glance).
4. **Human-in-the-loop is the design, not a fallback.** You cannot certify a label-free
   autosegmenter; you *can* certify a fast reviewer with good triage.
5. **Everything downstream of a seed is cheap** (~3 s propagation, registration cached), so we can
   afford many hypotheses per case.

## 3. Two problems, very different difficulty

| | Status |
|---|---|
| **Delineate** a boundary given a rough location | **Working** — MedSAM2 seed → propagate, Dice **0.73 (soft tissue, MR) – 0.89 (bone, CT)** |
| **Detect** *where* the tumour is, with no/bad seed | **The hard, unsolved half** — the focus of this pipeline |

SAM-style models don't *find* anything; they segment what you point at. So the pipeline is mostly
about **producing and vetting the seed**, then reusing the validated propagation.

## 4. Inputs / layers

- **CT, MR** — registered onto one grid (cached transform, see `register_ct_mr.register_cached`).
- **Seeds** — the noisy, multi-slice, possibly contradicting annotations.
- **Normal-anatomy envelope** — a body mask + an organ map (see §6). Label-free.

## 5. The pipeline

```
CT ─► body mask (HU threshold)            ┐
CT/MR ─► organ map (TotalSeg / OARs)      ├─ NORMAL-ANATOMY LAYER (§6)
                                          ┘        │
seeds ─►[1] reconcile ─►[2] propagate ─►[3] consensus + uncertainty
                                          │        │
                          [4] envelope-aware constraint ◄─────────┘
                                          │
                          [5] triage routing (+ anatomical location)
                                          │
                          [6] human confirm in workstation ──► correction = clean seed ↺
```

**[0] Register + place all layers on one grid.** CT, MR, seeds, and the organ map share the CT
geometry. Reuses the cached MR→CT transform.

**[1] Reconcile the noisy seeds.** Cluster annotations in 3D: nearby ones = one lesion (and give
its cross-slice extent); isolated ones = a separate candidate or an error. Keep every annotation
as an **independent prompt**, but pick the **largest-area one as the propagation anchor** (the
validated most-stable choice). Do **not** average or pick-one-and-hope. Contradictions are *typed*,
not silenced (§7).

**[2] Tissue-aware propagation — no tissue label needed.** Read the **CT Hounsfield units under
the seed** to choose the modality automatically: soft-tissue HU → propagate on **MR**; calcified /
bony → **CT**; ambiguous → run **both and fuse**. Crop to a **tight ROI** (the first-order drift
control — Dice collapsed 0.89 → 0.67 → 0.20 as the ROI loosened), then bidirectional MedSAM2 → 3D
candidate.

**[3] Consensus + uncertainty — where noise becomes a feature.** Propagate from **each real seed**
in the cluster and jitter each ([`--uncertainty`](medsam2-setup.md), scaled). Vote/STAPLE → one
consensus mask + a **per-voxel uncertainty map**. Independent seeds agreeing → high confidence;
contradicting seeds → high uncertainty exactly where they disagree.

**[4] Envelope-aware constraint (§6).** Reject candidate voxels **outside the body** (kills the
skin/background grab we saw uncropped), **downweight** those inside a symmetric, undistorted normal
organ, and **prune** anatomically implausible spans (through cortical bone / airway). A prior, not
a hard mask (§6 caveat).

**[5] Triage routing — the deliverable.** Per candidate emit
`{mask, confidence, uncertainty map, seeds-agreed/contradicted, host organ(s)}` and route:
- **seeds agree + coherent propagation** → *auto-accept, pending a glance*
- **seeds contradict / high uncertainty / propagation blew up or collapsed** → *NEEDS REVIEW (with reason)*
- **seedless / envelope-proposed** → *REVIEW* (low prior confidence)

Rank the queue by confidence so risky cases surface first (recall stays high without drowning the
reviewer). Report location anatomically — *"mass in left parotid, abuts mandible, displaces
airway"* — not raw coordinates.

**[6] Human-in-the-loop.** Show consensus + uncertainty over CT/MR/fusion in the existing
workstation; a confirm/correct click becomes a **clean seed** → re-propagate in seconds. Confirmed
masks accumulate into a small trustworthy label set that could later bootstrap a supervised model.

## 6. The normal-anatomy envelope

**Where it comes from (label-free):**
- **Body envelope** — from CT: `HU > −500`, largest connected component, fill holes. No model.
- **Organ map** — `TotalSegmentator` / `TotalSegmentator-MRI` (pretrained; generalizes because
  *normal* anatomy is consistent), or, on HaN-Seg today, the **25+ OAR masks each case ships**.

**What it does:**
- **False-positive suppression** — body reject + organ-aware prune (the biggest triage-precision
  win, and it fixes the exact failure we hit on day one).
- **Seedless detection** — *normal-anatomy subtraction* ("segment everything I can **name**; the
  tumour is the residual / the distortion") and *paired-organ asymmetry* (parotids, submandibular
  glands, eyes are bilateral — one enlarged/displaced/absent vs its mirror is an automatic seed).
- **Anatomically-aware ROI** — crop/propagate within the host organ region, a more meaningful drift
  boundary than a fixed box.
- **Reporting & routing** — name the location, list involved/abutted organs, compute involvement
  fractions (tumour ∩ organ / organ) as triage features.
- **Confidence anchors** — volume relative to host organ, depth-inside-body, overlap with normal
  tissue — sanity signals feeding the confidence score.

**Caveat:** TotalSegmentator segments **normal** organs; a tumour that *replaces* an organ makes
the organ prediction unreliable **exactly where you care most**. So the envelope is a
**prior/constraint that downweights and flags — never a hard mask that deletes** (deletion would
hurt recall, the one thing triage can't spend).

## 7. Handling the annotation pathologies (the crux)

- **Noisy seed** → treat as a **weak prompt**: jitter heavily, propagate, take consensus. Regions
  stable under perturbation are trustworthy.
- **Contradiction, typed:**
  - *same-slice* (two regions) → possibly **multifocal** → keep both candidates, flag;
  - *cross-slice* (inconsistent 3D shape) → propagate each seed independently; convergence = core,
    divergence = uncertain boundary;
  - *cross-organ* (seeds in different organs) → likely multifocal vs error → route to review with
    the organ context.
- **Seedless** → envelope-driven detection (§6), auto-routed to review.
- **Rule:** never resolve a contradiction by fiat; propagate the hypotheses and let the uncertainty
  map carry the disagreement to the human.

## 8. Validation without tumour labels

- **OAR proxies** — segment a *known* organ and score real Dice (done: mandible 0.89 CT / brainstem
  0.73 MR / parotid next). Detects machinery regressions with a real number.
- **Calibration** — the confidence must *mean* something: simulate noisy/contradicting seeds on the
  proxies and check **high uncertainty predicts high error**. If it does, triage routing is
  trustworthy; if not, fix it before it touches patients.
- **Test–retest** — same patient, jittered seeds → consistency.
- **Human override rate** — the real-world quality metric once in use.

## 9. Cost

Registration ~5 min CPU, **cached once per case** (`transform.tfm`). Propagation
**~15–27 ms/slice, ≤2.2 GB VRAM**, ~2–4 s/volume. Body mask is trivial; TotalSegmentator is a
one-time pass per case. **The cohort is registration-bound, not GPU-bound.**

## 10. What exists vs what's new

**Exists:** cached MR→CT registration; MedSAM2 seed→propagate harness with tight-ROI crop,
modality switch, consensus+uncertainty (`--uncertainty`), surface metrics; OAR masks; the CT/MR/
fusion workstation with multi-label seg display.

**New (build order — measure each on proxies before the next):**
1. **Envelope constraint** — body mask + OAR exclusion → measure **FP suppression** (precision ↑,
   recall ~flat) on the brainstem/parotid proxy. Re-tests the day-one failure directly.
2. **Consensus from multiple *real* (noisy) seeds** — does consensus beat any single noisy seed,
   and does uncertainty overlap the error? (§8 calibration.)
3. **Seed reconciliation** [1] — clustering + contradiction typing.
4. **Seedless detection** — normal-anatomy subtraction + paired-organ asymmetry.
5. **Triage routing + anatomical reporting** [5], surfaced in the workstation.

## 11. What this is *not*

Not a fire-and-forget autosegmenter, and not measurement-grade. Automatic **detection** stays
unreliable on CT+MR alone (PET would change that); the envelope is unreliable exactly at the
tumour; the design **depends on a human** confirming flagged cases. What it delivers is a
**high-recall, uncertainty-aware, anatomically-contextual triage** that turns scattered unreliable
annotations into a fast, honest review loop.

---

*Related: [CT+MR tumour segmentation](ct-mri-tumour-segmentation.md) ·
[MedSAM2 seed-test plan & results](medsam2-seed-test-plan.md) ·
[MedSAM2 setup & usage](medsam2-setup.md).*
