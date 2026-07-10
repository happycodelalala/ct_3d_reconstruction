"""MVP triage pipeline (good-conditions), end to end on one case.

Ref: docs/tumour-triage-pipeline.md §5. Given a case + good-quality seed slice(s), produce a
RECALL-SAFE 3D envelope and route it for human review:

  good seed(s) -> ensemble propagate -> consensus -> recall-safe envelope (dilate +Nmm)
              -> air-prune -> GT-free confidence -> {auto-accept | REVIEW} -> envelope.nrrd

Why dilate: MedSAM2 systematically UNDER-segments and agreement-uncertainty is blind to it
(§4). Dilating the delineation covers the miss; triage confirms/tightens. Only air is deleted
(a soft-tissue tumour is never air) — the one provably-safe hard delete (§6). Deferred (§9):
the registration-quality gate (needs failing-registration data to set a threshold) and a
connected-component body mask (its motive, uncropped-propagation leakage, is already handled
by the engine's tight ROI crop, leaving air-pruning sufficient here).

On OAR proxies (GT known) it also reports recall / precision / Dice so the pipeline stays
measurable. In production the seed comes from the annotation, not the GT.

    .venv/bin/python scripts/triage_pipeline.py \
        --case-dir hanseg_data/HaN-Seg/set_1/case_01 --oar Brainstem --modality mr --dilate-mm 2
"""
import argparse
import json
import os

import numpy as np
import SimpleITK as sitk
from scipy.ndimage import distance_transform_edt
from skimage import measure

from medsam2_seed_test import prepare_case, segment, dice, slice_areas

AIR_HU = -500.0  # below this HU is air — never tumour, safe to delete


def recall_safe_envelopes(consensus, radii, spacing_xyz):
    """{r_mm: dilated full-grid mask} for each radius. The EDT is computed only in a bbox
    around the consensus (+ max radius); a full-grid distance transform is O(volume) and
    needless since dilation only reaches voxels near the mask."""
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


def largest_cc_fraction(mask):
    """Fraction of mask voxels in its largest connected component — a GT-free coherence
    signal. A fragmented mask (propagation broke up) is low-confidence."""
    if not mask.any():
        return 0.0
    counts = np.bincount(measure.label(mask).flat)[1:]
    return float(counts.max() / counts.sum())


def mean_pairwise_dice(masks):
    """Mean pairwise Dice across the ensemble — GT-free seed-convergence signal."""
    if len(masks) < 2:
        return 1.0
    ds = [dice(masks[i], masks[j]) for i in range(len(masks)) for j in range(i + 1, len(masks))]
    return float(np.mean(ds))


def recall_precision(pred, gt):
    inter = float((pred.astype(bool) & gt.astype(bool)).sum())
    g, p = gt.sum(), pred.sum()
    return (round(inter / g, 4) if g else float("nan"),
            round(inter / p, 4) if p else float("nan"))


def main():
    ap = argparse.ArgumentParser(description="MVP triage pipeline (good-conditions) on one case.")
    ap.add_argument("--case-dir", required=True)
    ap.add_argument("--oar", default="Brainstem", help="proxy target / stand-in annotation source")
    ap.add_argument("--modality", choices=["mr", "ct"], default="mr")
    ap.add_argument("--crop-margin-mm", type=float, default=12.0)
    ap.add_argument("--ensemble", type=int, default=3, help="good central seeds")
    ap.add_argument("--jitter", type=int, default=3, help="mild seed placement noise (px)")
    ap.add_argument("--dilate-mm", type=float, default=2.0, help="recall-safe envelope radius")
    ap.add_argument("--converge-min", type=float, default=0.7, help="min mean pairwise Dice to auto-accept")
    ap.add_argument("--coherence-min", type=float, default=0.9, help="min largest-CC fraction to auto-accept")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    case = os.path.basename(os.path.normpath(a.case_dir))
    out_dir = a.out or os.path.join("runs", "triage", f"{case}_{a.oar.lower()}_{a.modality}")
    os.makedirs(out_dir, exist_ok=True)

    ctx = prepare_case(a.case_dir, oar=a.oar, modality=a.modality, crop_margin_mm=a.crop_margin_mm)

    # good-conditions seeds: the largest-area slices, lightly jittered (in production these
    # are the annotator's slices; here we stand them in from the OAR GT)
    areas = slice_areas(ctx.gt_full)
    zs = np.where(areas > 0)[0]
    slices = zs[np.argsort(areas[zs])[::-1][:a.ensemble]]
    masks = [segment(ctx, int(z), "mask", shift=a.jitter, rng=np.random.RandomState(100 + k))
             for k, z in enumerate(slices)]
    consensus = np.sum(masks, axis=0) >= (len(masks) / 2.0)   # majority vote

    # recall-safe envelope, then air-prune (the only safe delete)
    env_raw = recall_safe_envelopes(consensus, [a.dilate_mm], ctx.grid_img.GetSpacing())[a.dilate_mm]
    ct_arr = sitk.GetArrayFromImage(ctx.ct_vol) if ctx.ct_vol is not None else None
    envelope = env_raw & (ct_arr > AIR_HU) if ct_arr is not None else env_raw
    air_pruned = int((env_raw & ~envelope).sum())

    # GT-free confidence + routing
    converge = mean_pairwise_dice(masks)
    coherence = largest_cc_fraction(envelope)
    reasons = []
    if converge < a.converge_min:
        reasons.append(f"seeds diverge (pairwise Dice {converge:.2f})")
    if coherence < a.coherence_min:
        reasons.append(f"fragmented mask (largest-CC {coherence:.2f})")
    route = "REVIEW" if reasons else "auto-accept"

    report = {
        "case": case, "oar": a.oar, "modality": a.modality,
        "seed_slices": sorted(int(z) for z in slices), "dilate_mm": a.dilate_mm,
        "consensus_voxels": int(consensus.sum()), "envelope_voxels": int(envelope.sum()),
        "air_pruned_voxels": air_pruned,
        "seed_convergence": round(converge, 4), "coherence": round(coherence, 4),
        "route": route, "review_reasons": reasons,
    }
    # proxy evaluation (GT known) — not available in production
    rec, prec = recall_precision(envelope, ctx.gt_full)
    report["proxy"] = {"recall": rec, "precision": prec,
                       "dice": round(dice(envelope.astype(np.uint8), ctx.gt_full), 4),
                       "consensus_dice": round(dice(consensus.astype(np.uint8), ctx.gt_full), 4)}

    env_img = sitk.GetImageFromArray(envelope.astype(np.uint8))
    env_img.CopyInformation(ctx.grid_img)
    sitk.WriteImage(env_img, os.path.join(out_dir, "envelope.nrrd"))
    json.dump(report, open(os.path.join(out_dir, "report.json"), "w"), indent=2)

    print(f"\n=== triage: {case} / {a.oar} / {a.modality} ===")
    for k, v in report.items():
        if k != "proxy":
            print(f"  {k:20s} {v}")
    print(f"  proxy (GT) recall {rec}  precision {prec}  dice {report['proxy']['dice']}"
          f"  (consensus dice {report['proxy']['consensus_dice']})")
    print(f"  ROUTE: {route}" + (f"  — {'; '.join(reasons)}" if reasons else ""))
    print(f"  -> {out_dir}/envelope.nrrd, report.json")


if __name__ == "__main__":
    main()
