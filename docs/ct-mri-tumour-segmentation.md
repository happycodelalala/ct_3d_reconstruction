# CT + MRI Tumour Segmentation & Fusion (Head & Neck)

How ONCOVOL turns a **paired CT + MRI** study into a 3D tumour that lives in the same
space as both volumes — first by **aligning** the MR to the CT, then by **segmenting**
the tumour on the modality that can actually see it. This supersedes the CT-only
[single-slice propagation](head-and-neck-segmentation.md) whenever an MR is available.

> **TL;DR** — CT and MR are separate acquisitions in different scanner frames, so
> they must be **registered** before anything else (`scripts/register_ct_mr.py`:
> Mattes-MI, coarse cranio-caudal seed → rigid+affine, line-search + never-regress
> guard). Then the tumour is segmented on the **MR** (T1-C / T2 / DWI — where H&N
> tumour is visible, unlike CT), the resulting 3D mask is transferred into CT space,
> and `scripts/preprocess_hn_mri.py` meshes it and emits a dataset carrying **both**
> volumes so the workstation can show **CT / MR / fusion**.

---

## 1. Why add MRI

Plain CT under-determines the head & neck gross tumour volume (GTV): the tumour is
frequently **iso-dense** with adjacent muscle, vessels and nodes, which is exactly why
the [CT-only method](head-and-neck-segmentation.md) has to *assume* a 3D shape from one
annotated slice. **MRI removes that constraint** — T1 post-contrast, T2 and diffusion
give real soft-tissue contrast through the whole stack, so the tumour can be segmented
in genuine 3D instead of propagated. The moment an MR is in hand, the single-slice trick
is no longer the best you can do.

Adding MRI introduces exactly **two** problems, in order:

1. **Alignment** — put the MR into the CT's coordinate space (§3). This is the linchpin;
   if it is wrong, every downstream step is wrong.
2. **Segmentation** — produce a 3D tumour mask using the MR's contrast (§4).

---

## 2. Inputs and the contract

| Input | Format | Notes |
|---|---|---|
| CT volume | DICOM series / NIfTI / NRRD | the geometric reference (RT planning space) |
| MR volume(s) | DICOM series / NIfTI / NRRD | T1-C and/or T2 and/or DWI; own frame + FOV |
| Tumour mask *(if any)* | any 3D binary label | may be drawn on **either** modality |

The **CT stays the reference grid** — it defines the shared normalized world space the
app is built around (see [README → How it works](../README.md#how-it-works)), and it is
the radiotherapy planning geometry. The MR is brought *to* the CT, never the reverse.

---

## 3. Aligning CT and MRI (registration) — the linchpin

### 3.1 Why registration is required

CT and MR are acquired separately, so they arrive in **different scanner coordinate
frames and with different fields of view**. Concretely, on a HaN-Seg patient:

```
CT  Z ∈ [-759, -357] mm   extent 404 mm (head → shoulders)   1024×1024×202 @ 0.56×0.56×2.0
MR  Z ∈ [ -99, +147] mm   extent 249 mm (head only)           512× 512× 83 @ 0.70×0.70×3.0
```

The Z ranges are **disjoint** and the FOVs differ, so you cannot just trust the stored
origins (identity resampling would place them ~660 mm apart), and naïvely aligning
geometric *centres* matches the MR's head to the CT's *neck*. Registration is mandatory.

### 3.2 The method we use (`scripts/register_ct_mr.py`)

Register **MR → CT** so the MR lands on the CT grid:

1. **Similarity metric: Mattes mutual information.** Multimodal intensities don't
   correspond linearly (bone is bright on CT, dark on MR), so sum-of-squares is wrong;
   MI is the correct multimodal metric.
2. **Coarse cranio-caudal (Z) seed.** Because the FOVs differ, a whole-image search over
   the Z translation (±150 mm) first locks the head to the head — the axis where the
   centres disagree and gradient descent's capture range is too small to bridge.
3. **Rigid → affine refinement**, multi-resolution (shrink 4→2→1), with a
   **line-search** optimiser so it can't overshoot a good seed into a worse minimum.
4. **Never-regress guard.** Score the seed / rigid / affine transforms on the same
   metric and return whichever actually wins — so the result is never worse than the
   seed even if a stage misbehaves.

**Rigid+affine, deliberately not deformable.** A deformable (B-spline/SyN) registration
can warp the *tumour itself* to satisfy the metric (mass effect, FOV mismatch), which
corrupts the very thing you're measuring. Keep the transform rigid+affine so it can only
translate/rotate/scale/shear the whole MR; if the tumour is drawn on the MR, its shape
stays authoritative. Add a **masked** deformable refinement *outside* the lesion only if
residual alignment is visibly poor — it is not needed for the validated H&N cases.

**Which space to segment in.** Prefer segmenting on the MR **in its native space** (best
contrast, no resampling blur), then transfer only the resulting binary mask into CT space
with the registration transform. That keeps the tumour geometry crisp and lets rigid+affine
handle *placement* without deforming the lesion.

```bash
# align + write QA overlays for one case
.venv/bin/python scripts/register_ct_mr.py --case-dir <case>
```

### 3.3 Validating the alignment — do not skip this

Misregistration is the one failure that silently corrupts everything, so QA is built in.
`register_ct_mr.py` writes, per case:

| Output | What it tells you |
|---|---|
| `checker_after.png` | CT/MR checkerboard — anatomy should run **continuously** across tile seams |
| `overlay_after.png` | CT (green) + MR (magenta), soft-tissue windowed — aligned tissue reads **grey/white** |
| `mandible_on_MR.png` | the CT-defined **mandible contour** burned on the registered MR — a structure defined in CT space should land on its true location in MR (decisive check) |
| `qa.json` | MI before/after (more negative = better) |

This was validated across 5 HaN-Seg cases (MI improved 5/5; the mandible contour lands on
the MR jaw in every spot-check).

### 3.4 Rigid vs deformable — when to reach for each

| Approach | Pros | Cons |
|---|---|---|
| **Rigid (6-DOF)** | fast, robust, *cannot distort anatomy*; ideal if same immobilisation | can't fix neck flexion / jaw / swallow motion |
| **Affine (12-DOF)** | cheap extra global correction (scale/shear) | still global |
| **Deformable (B-spline/SyN)** | best local alignment | **can warp the tumour**; hard to validate; risk of folding — use masked, outside the lesion, only if needed |

---

## 4. Segmenting the tumour with both modalities

Registration done, the tumour mask itself is the **only** modality-specific step —
everything downstream (mesh → manifest → fusion) is generic (§5).

### 4.1 Which MR sequence

| Sequence | Role |
|---|---|
| **T1 post-contrast (T1-C)** | primary boundary channel — the H&N GTV workhorse |
| **T2 / T2-FS** | extent & oedema; good for a guided grow (tumour hyperintense) |
| **DWI / ADC** | discriminative (tumour restricts); low-res, EPI distortion → weaker for fine boundaries |

Delineate on **T1-C**, cross-check extent on T2/DWI. For a learned model, feed all
available sequences + CT as channels.

### 4.2 How to produce the 3D mask — three tiers

| Tier | Method | Trade-off |
|---|---|---|
| **Classical, MR-guided** *(no learned model)* | region-grow on T2/DWI seeded by a contour, bounded in 3D | keeps "no inference in the loop"; transparent; lower ceiling, can leak on oedema |
| **Interactive foundation model** ⭐ *(recommended)* | MedSAM2 / nnInteractive on the co-registered MR — a few prompts → real 3D mask | best quality-for-effort; **human verifies each case** (stays honest); needs GPU (on AMD hardware that's **ROCm/PyTorch**) |
| **Automatic multimodal** | CT+MR channels → a pretrained/trained nnU-Net (HECKTOR-style) | fully automatic, highest ceiling; needs paired training data + a validation set; black-box |

**Recommendation:** the interactive tier (MedSAM2 / nnInteractive) on the MR — genuine
3D quality with a human in the loop, no bespoke training. Keep the classical grow as a
no-GPU fallback; reserve automatic nnU-Net for when you have paired CT+MR training data.

### 4.3 What "good" looks like — benchmarks

Reference scores from public challenges (mean Dice; calibration, not promises — verify
before quoting formally):

| Challenge | Modality | Target | Top Dice | Note |
|---|---|---|---|---|
| **HECKTOR 2022** | PET/CT | H&N GTVp / GTVn | ≈0.80 / 0.78 (winner aggregate ≈0.77) | *the* H&N tumour benchmark — but PET/CT, not CT+MR |
| **HNTS-MRG 2024** | T2 MR only | H&N GTVp+n | **0.825** pre-RT · **0.733** mid-RT | top methods *beat clinician inter-observer* |
| **HaN-Seg 2023** | CT + MR | 30 H&N OARs | ≈0.77 mean | only CT+MR H&N challenge — organs, not tumour |

A realistic target for a real H&N tumour mask is **~0.75–0.83 Dice (at/above
inter-observer)** — versus our geometric envelope, which is not measurement-grade at all.

> **The catch that matters here:** the big *tumour* benchmarks are **single-modality by
> design** (HECKTOR = PET/CT, HNTS-MRG = T2-MR). There is **no public CT+MR-*fused* H&N GTV
> benchmark**, and — below — no off-the-shelf pretrained model for it either.

### 4.4 Pretrained models to reach for

**Tumour:**

| Model | Kind | CT / MR | Use |
|---|---|---|---|
| **MedSAM2** ⭐ | promptable 3D foundation (open weights) | CT + MR + PET | prompt on the MR → 3D mask; the recommended interactive path |
| **SAM-Med3D**, **SegVol** | promptable 3D universal | CT (+MR) | alternatives; SegVol adds text prompts (CT-centric) |
| **nnU-Net** + **HECKTOR** / **HNTS-MRG** winner weights | automatic, task-specific | PET/CT or T2-MR | strong automatic baselines; single-modality inputs |

**Organ / body envelope** — the app's "organ envelope" layer (today a CT threshold):

| Model | CT / MR | Note |
|---|---|---|
| **TotalSegmentator** | CT **and** MR | 100+ structures + a **`body` task** (trunk / skin) — a direct drop-in for the envelope mesh, far better than thresholding |
| **TotalSegmentator MRI** | MR (+CT) | 80 structures, sequence-independent, Dice ≈0.86 |
| **MRSegmentator** | MR + CT | 40 classes |

**"Multi-modality" ≠ "CT+MR fused."** Almost every model above is modality-*agnostic* (runs
on CT *or* MR), not jointly fused. To actually fuse both for the tumour you do **input-level
fusion** — register (→ §3), then stack CT + MR as **nnU-Net input channels** and
train/fine-tune; there is no pretrained CT+MR-fused H&N tumour model to grab. So in
practice: **MedSAM2 interactively on the MR** now, or a **channel-fused nnU-Net** once you
have paired training data. Either way, our registration (§3) is the enabler.

### 4.5 The integration point (unchanged)

Whatever tier you pick, the contract is identical to the CT-only path
([§10 there](head-and-neck-segmentation.md#10-extending-toward-real-segmentation)):

> **Produce a 3D binary tumour mask aligned to the CT grid.**

Segment in MR space → transfer the mask into CT space with the registration transform
(nearest-neighbour) → hand it to the meshing/manifest step. Nothing downstream changes.

---

## 5. The pipeline (`scripts/preprocess_hn_mri.py`)

Ties §3 and §4 together and emits a workstation dataset carrying **both** volumes:

```
 CT ─┐
     ├─▶ (1) register MR→CT (§3) ─▶ (2) resample CT, registered MR, and the tumour
 MR ─┘                                   mask into ONE shared 256×256×Z grid
                                                   │
                                                   ▼
                             (3) window each · marching-cubes the mask · metrics
                                                   │
                                                   ▼
                     public/data/<id>/  — manifest carries a 2nd `mri` volume per
                                          timepoint (+ `mriWL`); app shows CT/MR/fusion
```

- Resamples the CT, the registered MR (a **single** interpolation via the transform),
  and the mask into the shared grid; windows CT to a soft-tissue range and MR by robust
  percentiles; meshes the mask; measures metrics from the native-resolution mask.
- The manifest gains an optional per-timepoint **`mri`** volume and an **`mriWL`** default
  window/level. In the app a **CT / MR / FUSION** switch (with a CT↔MR blend slider)
  re-renders every 2D/3D view from the chosen volume, all from one shared `srcLum01()`.

```bash
# build a CT+MR dataset, then register it in the picker
.venv/bin/python scripts/preprocess_hn_mri.py --case-dir <case> --roi-glob '<mask>.nrrd' --roi-label tumour
npm run data:index
```

> **Current stand-in.** Because the validation dataset (HaN-Seg) ships **no tumour**, the
> pipeline defaults to the **mandible OAR as a tumour stand-in** — it exercises the whole
> CT+MR → register → build → fusion path on real data. Swap `--roi-glob` for a real tumour
> mask (from §4) and nothing else changes.

---

## 6. Recommended end-to-end workflow

1. **Align** MR → CT (`register_ct_mr.py`); **inspect the QA** (checkerboard + mandible
   contour) before trusting it.
2. **Segment** the tumour on the MR (interactive MedSAM2 / nnInteractive on T1-C), verify.
3. **Transfer** the mask to CT space with the registration transform.
4. **Build** the dataset (`preprocess_hn_mri.py`) and view CT / MR / fusion in the app.

> **Organ / body envelope (optional):** for the app's envelope layer, prefer
> **TotalSegmentator** (§4.4) on the CT or MR over an intensity threshold — it drops into
> the same meshing path and is a large quality upgrade for little effort.

---

## 7. Limitations & honest notes

- **Registration is the ceiling.** A tumour mask is only as trustworthy as the CT↔MR
  alignment under it — always eyeball the QA. Rigid+affine assumes no large soft-tissue
  motion between scans; if neck/jaw position differs a lot, add a masked deformable step.
- **Don't deform the tumour.** Segment in MR space and transfer the mask; never let a
  deformable registration reshape the lesion to match the metric.
- **Interactive ≠ automatic.** The recommended path keeps a human verifying each mask —
  honest, but not hands-off. Automatic nnU-Net needs paired CT+MR training + validation.
- **No fused pretrained tumour model exists.** Off-the-shelf models are single-modality
  or modality-agnostic; a true CT+MR-fused H&N GTV model must be trained (channel fusion)
  — until then, interactive (MedSAM2) is the realistic route. See §4.4.
- **GPU:** learned segmentation is PyTorch — on AMD hardware target **ROCm**, not CUDA.

---

## 8. Benchmarks & models — sources

Numbers in §4.3–4.4 are challenge/paper-reported (verify before formal use):

- HECKTOR 2022 (PET/CT H&N GTV): <https://pmc.ncbi.nlm.nih.gov/articles/PMC10171217/>
- HNTS-MRG 2024 (T2-MR H&N GTV): <https://arxiv.org/abs/2411.18585>
- HaN-Seg 2023 challenge (CT+MR OARs): <https://han-seg2023.grand-challenge.org/official-results-of-the-han-seg-challenge/>
- TotalSegmentator (CT & MR, incl. `body`): <https://github.com/wasserth/TotalSegmentator>
- TotalSegmentator MRI: <https://arxiv.org/abs/2405.19492>
- MRSegmentator: <https://arxiv.org/abs/2405.06463>
- MedSAM2: <https://arxiv.org/abs/2504.03600>
- SegVol: <https://arxiv.org/abs/2311.13385> · SAM-Med3D: <https://arxiv.org/abs/2310.15161>

---

*Registration: [`scripts/register_ct_mr.py`](../scripts/register_ct_mr.py). Pipeline:
[`scripts/preprocess_hn_mri.py`](../scripts/preprocess_hn_mri.py). CT-only single-slice
method: [`docs/head-and-neck-segmentation.md`](head-and-neck-segmentation.md). Shared
coordinate space & manifest: [`README.md`](../README.md).*
