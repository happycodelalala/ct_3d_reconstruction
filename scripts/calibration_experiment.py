"""Calibration experiment — the load-bearing test for the triage pipeline.

Ref: docs/tumour-triage-pipeline.md §4 (the assumption) and §8 (validate it first).

Question: is consensus + per-voxel uncertainty, computed from NOISY seeds, calibrated —
does high uncertainty actually predict where the mask is wrong? If not, confidence
routing is worthless and we stop before building further.

Method (on an OAR proxy, GT known — no tumour labels needed): simulate an ensemble of
realistic bad seeds — different slices (annotators pick whatever slice), imprecise
placement (jitter), and a fraction with gross shifts (contradictions) — propagate each
with the validated engine, vote -> consensus + uncertainty, and measure:
  Q1  does consensus beat the best single noisy seed's Dice?
  Q2  does high uncertainty predict the error region?  (AUROC, the load-bearing test)

    .venv/bin/python scripts/calibration_experiment.py \
        --case-dir hanseg_data/HaN-Seg/set_1/case_01 --oar Brainstem --modality mr
"""
import argparse
import json
import os

import numpy as np
from scipy.stats import rankdata
from scipy.ndimage import distance_transform_edt

# reuse the validated segmentation engine (setup + one-seed propagation)
from medsam2_seed_test import prepare_case, segment, dice


def sampled_seed_slices(gt_full, k, rng, central=False):
    """k slice indices with the target present. central=False (noisy conditions) samples
    across the whole z-extent — an annotator marks whatever slice; central=True (good
    conditions) takes the k largest-area slices, i.e. well-chosen annotations."""
    areas = gt_full.reshape(gt_full.shape[0], -1).sum(1)
    zs = np.where(areas > 0)[0]
    if central:
        return zs[np.argsort(areas[zs])[::-1][:k]]
    return rng.choice(zs, size=k, replace=len(zs) < k)


def dilate_envelopes(consensus, radii, spacing_xyz):
    """{r_mm: dilated full-grid mask} for each radius. The EDT is computed only in a bbox
    around the consensus (+ max radius) — a full-grid distance transform is O(volume) and
    needless, since dilation only reaches voxels near the mask."""
    sx, sy, sz = spacing_xyz
    if not consensus.any():
        return {r: consensus.copy() for r in radii}
    rmax = max(radii)
    mz, my, mx = (int(np.ceil(rmax / s)) + 1 for s in (sz, sy, sx))
    zz, yy, xx = np.where(consensus)
    z0, z1 = max(0, zz.min() - mz), min(consensus.shape[0], zz.max() + 1 + mz)
    y0, y1 = max(0, yy.min() - my), min(consensus.shape[1], yy.max() + 1 + my)
    x0, x1 = max(0, xx.min() - mx), min(consensus.shape[2], xx.max() + 1 + mx)
    sub = consensus[z0:z1, y0:y1, x0:x1]
    edt = distance_transform_edt(~sub, sampling=(sz, sy, sx))
    out = {}
    for r in radii:
        full = np.zeros(consensus.shape, bool)
        full[z0:z1, y0:y1, x0:x1] = sub if r == 0 else (edt <= r)
        out[r] = full
    return out


def auroc(scores, labels):
    """AUROC of a continuous score predicting a boolean label, tie-aware (Mann-Whitney).
    Here: does per-voxel uncertainty rank error voxels above correct ones?"""
    pos, neg = int(labels.sum()), int((~labels).sum())
    if pos == 0 or neg == 0:
        return float("nan")
    r = rankdata(scores)  # average ranks handle uncertainty's many ties
    return float((r[labels].sum() - pos * (pos + 1) / 2) / (pos * neg))


def main():
    ap = argparse.ArgumentParser(description="Uncertainty-calibration test on an OAR proxy.")
    ap.add_argument("--case-dir", required=True)
    ap.add_argument("--oar", default="Brainstem")
    ap.add_argument("--modality", choices=["mr", "ct"], default="mr")
    ap.add_argument("--crop-margin-mm", type=float, default=12.0)
    ap.add_argument("--ensemble", type=int, default=8, help="number of noisy seeds")
    ap.add_argument("--jitter", type=int, default=10, help="normal placement noise (px)")
    ap.add_argument("--contradict-frac", type=float, default=0.25, help="fraction of seeds grossly wrong")
    ap.add_argument("--contradict-shift", type=int, default=35, help="gross-error shift (px)")
    ap.add_argument("--central", action="store_true", help="good conditions: seeds on the largest-area slices")
    ap.add_argument("--dilate-mm", default="2,4", help="recall-safe envelope radii (mm), comma-separated")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="runs/calibration")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    ctx = prepare_case(a.case_dir, oar=a.oar, modality=a.modality, crop_margin_mm=a.crop_margin_mm)
    gt = ctx.gt_full.astype(bool)
    rng = np.random.RandomState(a.seed)
    slices = sampled_seed_slices(ctx.gt_full, a.ensemble, rng, central=a.central)
    n_bad = int(round(a.contradict_frac * a.ensemble))
    bad = set(rng.choice(a.ensemble, size=n_bad, replace=False).tolist()) if n_bad else set()
    print(f"[{a.oar}/{a.modality}] ensemble={a.ensemble} slices={sorted(int(s) for s in slices)}"
          f" contradicting={sorted(bad)}")

    # --- run the noisy ensemble ------------------------------------------------
    singles, acc = [], np.zeros(ctx.full_shape, np.float32)
    for k, z in enumerate(slices):
        shift = a.contradict_shift if k in bad else a.jitter
        m = segment(ctx, int(z), "mask", shift=shift, rng=np.random.RandomState(1000 + k))
        d = dice(m, ctx.gt_full)
        singles.append(d)
        acc += m
        print(f"  seed {k} z={int(z):3d} shift={shift:2d}{'  (contradict)' if k in bad else ''}  Dice {d:.3f}")

    vote = acc / a.ensemble                    # per-voxel fraction predicting positive
    consensus = vote >= 0.5
    uncertainty = 1.0 - np.abs(2.0 * vote - 1.0)   # 0 at full agreement, 1 at a 50/50 split

    # --- error anatomy, within the region where there is signal or GT ----------
    # Split errors by TYPE (miss vs false-positive) and by whether the seeds AGREE.
    # Agreement-based uncertainty can only flag DISAGREEMENT errors; errors where every
    # seed agrees (systematic bias) are "blind". Missed tumour that is blind = a *silent
    # miss* — the dangerous class for triage.
    R = (vote > 0) | gt
    err = (consensus != gt) & R
    fn = (~consensus) & gt                     # missed tumour
    fp = consensus & (~gt)                      # false positive
    blind = (vote == 0) | (vote == 1)          # full agreement → zero uncertainty
    au = auroc(uncertainty[R], err[R])
    cons_dice = dice(consensus.astype(np.uint8), ctx.gt_full)

    def frac(a_, b_):
        return round(float(a_.sum() / b_.sum()), 4) if b_.sum() else float("nan")

    res = {
        "oar": a.oar, "modality": a.modality, "ensemble": a.ensemble,
        "contradict_frac": a.contradict_frac, "contradicting_seeds": sorted(bad),
        # Q1 — is the ensemble worth it?
        "best_single_dice": round(max(singles), 4),
        "mean_single_dice": round(float(np.mean(singles)), 4),
        "consensus_dice": round(cons_dice, 4),
        "consensus_beats_best_single": bool(cons_dice > max(singles)),
        # Q2 — is uncertainty calibrated? (the load-bearing test)
        "auroc_uncertainty_predicts_error": round(au, 4),
        "error_voxels": int(err.sum()),
        "blind_error_frac": frac(err & blind, err),        # errors uncertainty cannot see
        "fn_voxels": int(fn.sum()), "fp_voxels": int(fp.sum()),
        "silent_miss_frac": frac(fn & blind, fn),          # missed tumour that is silent
        "roi_voxels": int(R.sum()),
    }

    # --- recall-safe envelope: dilate the consensus to cover systematic under-seg -----
    # Since the model's error is under-segmentation, a few-mm dilation (in physical space,
    # spacing-aware) trades precision for the recall triage needs. This targets the actual
    # failure (silent misses) directly, where variance-uncertainty could not.
    def recall_prec(mask):
        inter = float((mask & gt).sum())
        return (round(inter / gt.sum(), 4) if gt.sum() else float("nan"),
                round(inter / mask.sum(), 4) if mask.sum() else float("nan"))

    radii = [0.0] + [float(x) for x in a.dilate_mm.split(",") if x.strip()]
    envelopes = dilate_envelopes(consensus, radii, ctx.grid_img.GetSpacing())
    res["recall_safe"] = []
    for r in radii:
        rec, prec = recall_prec(envelopes[r])
        res["recall_safe"].append({"dilate_mm": r, "recall": rec, "precision": prec,
                                   "dice": round(dice(envelopes[r].astype(np.uint8), ctx.gt_full), 4)})
    json.dump(res, open(os.path.join(a.out, "calibration.json"), "w"), indent=2)

    print("\n=== calibration ===")
    for k, v in res.items():
        print(f"  {k:34s} {v}")
    passed = au >= 0.7 and res["silent_miss_frac"] < 0.2
    print(f"\n  Q1  consensus > best single seed?   {'YES' if res['consensus_beats_best_single'] else 'no'}"
          f"  ({res['consensus_dice']} vs {res['best_single_dice']})")
    print(f"  Q2  uncertainty predicts error?     {'PASS' if passed else 'FAIL'}"
          f"  (AUROC={au:.3f}; {res['silent_miss_frac']:.0%} of missed tumour is a SILENT miss)")
    print("\n  recall-safe envelope (dilate mm -> recall / precision / dice):")
    for e in res["recall_safe"]:
        print(f"    +{e['dilate_mm']:>3} mm   recall {e['recall']:.3f}   precision {e['precision']:.3f}"
              f"   dice {e['dice']:.3f}")
    print(f"  -> {a.out}/calibration.json")


if __name__ == "__main__":
    main()
