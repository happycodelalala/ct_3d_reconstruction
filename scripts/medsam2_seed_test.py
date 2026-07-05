"""MedSAM2 zero-shot seed test — validate the §4.5 recipe on real 3D ground truth.

Rationale & plan: docs/medsam2-seed-test-plan.md. In one sentence: take ONE mandible
slice as a "seed", feed it to MedSAM2 as a prompt, propagate bidirectionally through
the registered MR volume, and score volumetric Dice against the FULL mandible mask.
The mandible is a stand-in for a tumour we have no labels for — a clean upper-bound
sanity check that the promptable-propagation MECHANISM works on our own CT+MR data.

Two input routes (either works):
  # raw HaN-Seg case → register MR->CT inline (reuses register_ct_mr.register)
  .venv/bin/python scripts/medsam2_seed_test.py --case-dir hanseg_data/HaN-Seg/set_1/case_01

  # already-registered fast path
  .venv/bin/python scripts/medsam2_seed_test.py \
      --mr hanseg_data/registration_check/case_01/mr_in_ct.nrrd \
      --mask hanseg_data/HaN-Seg/set_1/case_01/case_01_OAR_Bone_Mandible.seg.nrrd

Planned experiments (docs §4):
  A  --prompt mask                     best-case ceiling of the mechanism
  B  --prompt box                      quantify the mask-vs-box gap
  C  --prompt mask --seed-slice N      off-centre seed → does it drift?
  D  --prompt mask --uncertainty 8     jittered-prompt consensus + uncertainty map

Outputs (under --out, default runs/medsam2_seed/<case>_<prompt>):
  pred_mask.nrrd     predicted 3D mask on the CT grid
  qa.png             pred(red) vs GT(green) contours on MR, several axial slices
  uncertainty.nrrd   per-voxel disagreement map           (only with --uncertainty)
  metrics.json       Dice (+ optional surface metrics), seed slice, settings
and prints the metrics.
"""
import argparse
import glob
import json
import os
import sys
import time

import numpy as np
from PIL import Image
import SimpleITK as sitk

# scripts/ is on sys.path (this file lives here) → reuse the cached registration and the
# robust per-slice MR window used for QA (identical logic, don't re-copy it).
from register_ct_mr import register_cached, load_ct_mr, _mr_slice_u8

# Make the vendored MedSAM2 `sam2` package importable without a setup.py install:
# sam2/__init__.py self-registers its hydra config module on import, so PYTHONPATH
# to the MedSAM2 repo root is all that's needed.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MEDSAM2 = os.path.join(_REPO, "MedSAM2")
if _MEDSAM2 not in sys.path:
    sys.path.insert(0, _MEDSAM2)

MODEL_SIZE = 512  # MedSAM2 tiny-hiera config is 512², not 1024²
IMG_MEAN = (0.485, 0.456, 0.406)  # ImageNet — MedSAM2 keeps SAM2's normalization
IMG_STD = (0.229, 0.224, 0.225)


# --------------------------------------------------------------------------- IO
def _resample_to_grid(img, ref, is_mask):
    """Resample `img` onto `ref`'s grid by physical space (identity transform).
    OAR segs are cropped to their bounding box, so this is how the mask is placed
    correctly on the CT grid (same trick as register_ct_mr.mandible_qa)."""
    interp = sitk.sitkNearestNeighbor if is_mask else sitk.sitkLinear
    dtype = sitk.sitkUInt8 if is_mask else sitk.sitkFloat32
    return sitk.Resample(sitk.Cast(img, dtype), ref, sitk.Transform(), interp, 0, dtype)


def load_inputs(args):
    """Return (grid_img, mr_vol, mask_vol, ct_vol_or_None), all on one grid.
    grid_img defines the output geometry; mr_vol is the MR intensity volume; mask_vol
    is the binary GT mandible; ct_vol feeds --modality ct (None on the --mr/--mask fast
    path unless --ct is given)."""
    if args.case_dir:
        case = os.path.basename(os.path.normpath(args.case_dir))
        ct, mr = load_ct_mr(args.case_dir)
        tx = register_cached(ct, mr, case)
        mr_vol = sitk.Resample(mr, ct, tx, sitk.sitkLinear, 0.0, sitk.sitkFloat32)
        cand = glob.glob(os.path.join(args.case_dir, f"*OAR_{args.oar}*.nrrd"))
        if not cand:
            raise SystemExit(f"no *OAR_{args.oar}*.nrrd in {args.case_dir}")
        mask_vol = _resample_to_grid(sitk.ReadImage(cand[0]), ct, is_mask=True)
        return ct, mr_vol, mask_vol, ct
    # fast path: pre-registered MR + a mask, aligned to the MR grid
    mr_vol = sitk.ReadImage(args.mr, sitk.sitkFloat32)
    mask_vol = _resample_to_grid(sitk.ReadImage(args.mask), mr_vol, is_mask=True)
    ct_vol = _resample_to_grid(sitk.ReadImage(args.ct), mr_vol, is_mask=False) if args.ct else None
    return mr_vol, mr_vol, mask_vol, ct_vol


# --------------------------------------------------------------- preprocessing
def mr_to_uint8(mr_arr):
    """Robust-percentile intensity window of the whole MR volume → uint8 [0,255].
    Same robust window the registration QA uses, so a few bright voxels don't blow
    the contrast out and starve MedSAM2 of tissue signal."""
    nz = mr_arr[mr_arr > 0]
    lo, hi = (np.percentile(nz, 2.0), np.percentile(nz, 99.5)) if nz.size else (0.0, 1.0)
    hi = max(hi, lo + 1e-3)
    v = np.clip(mr_arr, lo, hi)
    return ((v - lo) / (hi - lo) * 255.0).astype(np.uint8)


def ct_to_uint8(ct_arr, lo=-500.0, hi=1300.0):
    """Bone-emphasis CT window → uint8. Cortical bone (~1000+ HU) goes near-white and
    soft tissue mid-grey, so the mandible is a crisp bright target — the opposite of
    T1 MR, where cortical bone is a signal void."""
    v = np.clip(ct_arr, lo, hi)
    return ((v - lo) / (hi - lo) * 255.0).astype(np.uint8)


def to_model_input(vol_u8):
    """(D,H,W) uint8 → (D,3,512,512) float, RGB-replicated, ImageNet-normalized, on GPU."""
    import torch
    d, h, w = vol_u8.shape
    out = np.zeros((d, 3, MODEL_SIZE, MODEL_SIZE), dtype=np.float32)
    for i in range(d):
        im = Image.fromarray(vol_u8[i]).convert("RGB").resize((MODEL_SIZE, MODEL_SIZE))
        out[i] = np.array(im).transpose(2, 0, 1)
    t = torch.from_numpy(out / 255.0).cuda()
    mean = torch.tensor(IMG_MEAN, dtype=torch.float32)[:, None, None].cuda()
    std = torch.tensor(IMG_STD, dtype=torch.float32)[:, None, None].cuda()
    return (t - mean) / std


def pick_seed_slice(mask_arr, override):
    """Default seed = the mandible's largest-area axial slice (the center-outward
    anchor the literature says is most stable). --seed-slice forces an off-centre
    seed to observe drift (experiment C)."""
    areas = mask_arr.reshape(mask_arr.shape[0], -1).sum(axis=1)
    if override is not None:
        if not (0 <= override < mask_arr.shape[0]) or areas[override] == 0:
            raise SystemExit(f"--seed-slice {override} has no mandible (areas nonzero "
                             f"in z=[{int(np.argmax(areas>0))}..{mask_arr.shape[0]-1-int(np.argmax(areas[::-1]>0))}])")
        return override
    return int(np.argmax(areas))


def roi_crop(gt_arr, spacing_xyz, margin_mm):
    """Axis-aligned crop to the target's 3D bbox + `margin_mm` on each side.
    Why: the MR head sits in a small part of the big CT grid (mostly black), and
    unbounded propagation wanders far past the mandible in z. Cropping makes the
    object a sane fraction of 512² and localizes propagation — the same reason the
    reference CT-lesion script runs on a slab, not the whole body. Returns a tuple
    of slice() objects (z, y, x)."""
    sx, sy, sz = spacing_xyz  # sitk spacing is (x, y, z)
    mz, my, mx = int(round(margin_mm / sz)), int(round(margin_mm / sy)), int(round(margin_mm / sx))
    zz, yy, xx = np.where(gt_arr > 0)
    D, H, W = gt_arr.shape
    return (slice(max(0, zz.min() - mz), min(D, zz.max() + 1 + mz)),
            slice(max(0, yy.min() - my), min(H, yy.max() + 1 + my)),
            slice(max(0, xx.min() - mx), min(W, xx.max() + 1 + mx)))


def slice_bbox(mask2d, shift=0, rng=None):
    """[x_min,y_min,x_max,y_max] of a 2D mask, optionally jittered by up to `shift` px."""
    ys, xs = np.where(mask2d > 0)
    h, w = mask2d.shape
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    if shift and rng is not None:
        x0 -= rng.randint(0, shift + 1); x1 += rng.randint(0, shift + 1)
        y0 -= rng.randint(0, shift + 1); y1 += rng.randint(0, shift + 1)
    return np.array([max(0, x0), max(0, y0), min(w - 1, x1), min(h - 1, y1)])


# --------------------------------------------------------------- MedSAM2 run
def build_prompt(kind, mask_arr, seed_idx, shift=0, rng=None):
    """Prompt payload from the seed slice's mask — pure data, no predictor:
    ("mask", 2D bool array) or ("box", [x0,y0,x1,y1]). shift/rng translate it by a
    small random offset for the jittered uncertainty runs (experiment D)."""
    seed_mask2d = mask_arr[seed_idx].astype(bool)
    if kind == "box":
        return ("box", slice_bbox(seed_mask2d, shift, rng))
    m = seed_mask2d
    if shift and rng is not None:
        m = np.zeros_like(seed_mask2d)
        dy, dx = rng.randint(-shift, shift + 1), rng.randint(-shift, shift + 1)
        ys, xs = np.where(seed_mask2d)
        ys, xs = np.clip(ys + dy, 0, m.shape[0] - 1), np.clip(xs + dx, 0, m.shape[1] - 1)
        m[ys, xs] = True
    return ("mask", m)


def apply_prompt(predictor, state, fidx, prompt):
    """Apply a build_prompt() payload to `state` (obj_id=1): mask → add_new_mask,
    box → add_new_points_or_box. The predictor is passed in — no module globals."""
    import torch
    kind, payload = prompt
    if kind == "mask":
        predictor.add_new_mask(inference_state=state, frame_idx=fidx, obj_id=1,
                               mask=torch.from_numpy(payload))
    else:
        predictor.add_new_points_or_box(inference_state=state, frame_idx=fidx, obj_id=1,
                                        box=payload)


def propagate(predictor, state, seed_idx, prompt, D, H, W):
    """Apply the seed prompt and propagate forward + backward. Mirrors
    medsam2_infer_3D_CT.py: reset + re-apply between the two directions.
    Returns a (D,H,W) uint8 mask."""
    import torch
    seg = np.zeros((D, H, W), dtype=np.uint8)
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        apply_prompt(predictor, state, seed_idx, prompt)
        for fidx, _, logits in predictor.propagate_in_video(state):
            seg[fidx] = (logits[0] > 0.0).cpu().numpy()[0]
        predictor.reset_state(state)
        apply_prompt(predictor, state, seed_idx, prompt)
        for fidx, _, logits in predictor.propagate_in_video(state, reverse=True):
            seg[fidx] = (logits[0] > 0.0).cpu().numpy()[0]
        predictor.reset_state(state)
    return seg


# ------------------------------------------------------------------ scoring
def dice(a, b):
    a, b = a.astype(bool), b.astype(bool)
    inter = np.logical_and(a, b).sum()
    denom = a.sum() + b.sum()
    return float(2 * inter / denom) if denom else 1.0


def surface_metrics(pred, gt, spacing):
    """Surface-Dice(@2mm), ASSD, HD95 via SimpleITK signed-distance maps."""
    p = sitk.GetImageFromArray(pred.astype(np.uint8)); p.SetSpacing(spacing)
    g = sitk.GetImageFromArray(gt.astype(np.uint8)); g.SetSpacing(spacing)
    if pred.sum() == 0 or gt.sum() == 0:
        return {"assd_mm": None, "hd95_mm": None, "surface_dice_2mm": 0.0}
    pc = sitk.LabelContour(p); gc = sitk.LabelContour(g)
    pd = sitk.Abs(sitk.SignedMaurerDistanceMap(p, squaredDistance=False, useImageSpacing=True))
    gd = sitk.Abs(sitk.SignedMaurerDistanceMap(g, squaredDistance=False, useImageSpacing=True))
    d_p2g = sitk.GetArrayViewFromImage(gd)[sitk.GetArrayViewFromImage(pc) > 0]
    d_g2p = sitk.GetArrayViewFromImage(pd)[sitk.GetArrayViewFromImage(gc) > 0]
    alld = np.concatenate([d_p2g, d_g2p]) if d_p2g.size and d_g2p.size else np.array([np.nan])
    tol = 2.0
    sd = ((d_p2g <= tol).sum() + (d_g2p <= tol).sum()) / (d_p2g.size + d_g2p.size)
    return {"assd_mm": float(np.mean(alld)), "hd95_mm": float(np.percentile(alld, 95)),
            "surface_dice_2mm": float(sd)}


def largest_cc(mask):
    from skimage import measure
    if mask.sum() == 0:
        return mask
    lab = measure.label(mask)
    return (lab == (np.argmax(np.bincount(lab.flat)[1:]) + 1)).astype(np.uint8)


# ------------------------------------------------------------------- QA image
def write_qa(grid_img, mr_vol, pred_arr, gt_arr, seed_idx, out_png):
    """pred(red) + GT(green) contours on the MR, at a spread of axial slices."""
    zs = np.where(gt_arr.sum(axis=(1, 2)) > 0)[0]
    picks = sorted(set(int(z) for z in
                       [zs[0], zs[len(zs) // 4], seed_idx, zs[3 * len(zs) // 4], zs[-1]]))
    pred_img = sitk.GetImageFromArray(pred_arr.astype(np.uint8)); pred_img.CopyInformation(grid_img)
    gt_img = sitk.GetImageFromArray(gt_arr.astype(np.uint8)); gt_img.CopyInformation(grid_img)
    tiles = []
    for z in picks:
        mr = sitk.Flip(_mr_slice_u8(mr_vol[:, :, z]), [False, True])
        pc = sitk.Cast(sitk.Flip(sitk.BinaryContour(pred_img[:, :, z] > 0, fullyConnected=True),
                                 [False, True]) > 0, sitk.sitkUInt8)
        gc = sitk.Cast(sitk.Flip(sitk.BinaryContour(gt_img[:, :, z] > 0, fullyConnected=True),
                                 [False, True]) > 0, sitk.sitkUInt8)
        base = mr * (1 - sitk.Cast((pc | gc) > 0, sitk.sitkUInt8))
        rgb = sitk.Compose(base + pc * 255, base + gc * 255, base)  # R=pred, G=GT
        tiles.append(rgb)
    sitk.WriteImage(sitk.Tile(tiles, [len(tiles), 1], 0), out_png)
    return picks


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="MedSAM2 zero-shot seed test on HaN-Seg mandible.")
    src = ap.add_argument_group("input (one route)")
    src.add_argument("--case-dir", help="raw HaN-Seg case dir → register MR->CT inline")
    src.add_argument("--mr", help="pre-registered MR volume (mr_in_ct.nrrd)")
    src.add_argument("--mask", help="mandible OAR mask (.seg.nrrd)")
    src.add_argument("--ct", help="optional CT (enables --modality ct on the --mr/--mask route)")
    ap.add_argument("--prompt", choices=["mask", "box"], default="mask", help="seed prompt kind")
    ap.add_argument("--modality", choices=["mr", "ct"], default="mr",
                    help="volume MedSAM2 propagates through: mr (recipe) or ct "
                         "(mechanism check — mandible is crisp bone on CT, a void on MR)")
    ap.add_argument("--oar", default="Bone_Mandible",
                    help="target OAR on the --case-dir route (substring of its .seg.nrrd "
                         "filename), e.g. Brainstem, Parotid_L, Glnd_Submand_L. Soft-tissue "
                         "OARs are the fair MR test; bone is a T1 void")
    ap.add_argument("--seed-slice", type=int, default=None, help="force seed slice (off-centre = exp C)")
    ap.add_argument("--crop-margin-mm", type=float, default=24.0,
                    help="crop to the mandible ROI + this margin (mm) before inference; "
                         "keeps the object a sane fraction of 512² and bounds z-propagation")
    ap.add_argument("--no-crop", action="store_true", help="feed the whole volume (reproduces the over-segmentation failure)")
    ap.add_argument("--uncertainty", type=int, default=0, metavar="N", help="N jittered runs → consensus + map")
    ap.add_argument("--jitter", type=int, default=12, help="max prompt jitter in px (uncertainty runs)")
    ap.add_argument("--no-largest-cc", action="store_true", help="skip largest-connected-component cleanup")
    ap.add_argument("--surface", action="store_true", help="also compute ASSD / HD95 / surface-Dice")
    ap.add_argument("--checkpoint", default=os.path.join(_MEDSAM2, "checkpoints", "MedSAM2_latest.pt"))
    ap.add_argument("--cfg", default="configs/sam2.1_hiera_t512.yaml")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if not a.case_dir and not (a.mr and a.mask):
        ap.error("provide --case-dir OR both --mr and --mask")

    case = os.path.basename(os.path.normpath(a.case_dir or a.mr)).split(".")[0].replace("_IMG_MR_T1", "")
    oar_tag = "" if a.oar == "Bone_Mandible" else f"_{a.oar.lower()}"
    out_dir = a.out or os.path.join("runs", "medsam2_seed", f"{case}{oar_tag}_{a.modality}_{a.prompt}"
                                    + (f"_s{a.seed_slice}" if a.seed_slice is not None else ""))
    os.makedirs(out_dir, exist_ok=True)

    clk = time.perf_counter
    t_start = clk()
    timing = {}
    _t = clk()
    grid_img, mr_vol, mask_vol, ct_vol = load_inputs(a)
    timing["load_register_s"] = round(clk() - _t, 2)
    mr_arr = sitk.GetArrayFromImage(mr_vol)  # kept as the QA display base
    gt_arr = (sitk.GetArrayFromImage(mask_vol) > 0).astype(np.uint8)
    # intensity volume MedSAM2 actually propagates through, per --modality
    if a.modality == "ct":
        if ct_vol is None:
            raise SystemExit("--modality ct requires CT (use --case-dir, or add --ct on the fast path)")
        vol_arr, win_fn = sitk.GetArrayFromImage(ct_vol), ct_to_uint8
    else:
        vol_arr, win_fn = mr_arr, mr_to_uint8
    D, H, W = vol_arr.shape
    if gt_arr.sum() == 0:
        raise SystemExit(f"{a.oar} mask is empty on the grid — check inputs/registration")
    seed_full = pick_seed_slice(gt_arr, a.seed_slice)
    print(f"[{case}] oar={a.oar}  modality={a.modality}  grid {D}x{H}x{W}  target voxels {int(gt_arr.sum())}"
          f"  seed slice z={seed_full}  (largest-area z={int(np.argmax(gt_arr.reshape(D,-1).sum(1)))})")

    # Crop to the mandible ROI (+margin) before inference; keep the full-grid arrays
    # to paste the prediction back and to score/QA on the original geometry.
    full_shape, gt_full = (D, H, W), gt_arr
    if a.no_crop:
        cz = cy = cx = slice(None)
    else:
        cz, cy, cx = roi_crop(gt_arr, grid_img.GetSpacing(), a.crop_margin_mm)
        vol_arr, gt_arr = vol_arr[cz, cy, cx], gt_arr[cz, cy, cx]
        D, H, W = vol_arr.shape
        print(f"  crop -> z[{cz.start}:{cz.stop}] y[{cy.start}:{cy.stop}] x[{cx.start}:{cx.stop}]"
              f"  = {D}x{H}x{W}  (mandible now {gt_arr.sum()/gt_arr.size*100:.2f}% of ROI voxels)")
    seed_idx = seed_full - (cz.start or 0)  # seed in cropped coords for inference

    # build predictor + bind the prompt adders to it
    import torch
    torch.set_float32_matmul_precision("high")
    from sam2.build_sam import build_sam2_video_predictor_npz
    print("  building MedSAM2 predictor…")
    _t = clk()
    predictor = build_sam2_video_predictor_npz(a.cfg, a.checkpoint)
    torch.cuda.synchronize()
    timing["build_predictor_s"] = round(clk() - _t, 2)

    _t = clk()
    img = to_model_input(win_fn(vol_arr))
    torch.cuda.synchronize()
    timing["preprocess_s"] = round(clk() - _t, 2)
    print(f"  input {tuple(img.shape)} on {img.device}")

    def run_once(shift=0, rng=None):
        state = predictor.init_state(img, H, W)
        prompt = build_prompt(a.prompt, gt_arr, seed_idx, shift, rng)
        return propagate(predictor, state, seed_idx, prompt, D, H, W)

    # paste a cropped ROI prediction back onto the full grid (identity if --no-crop)
    def to_full(p):
        f = np.zeros(full_shape, np.uint8)
        f[cz, cy, cx] = p
        return f

    # ---- main run (experiment A/B/C) --------------------------------------
    torch.cuda.reset_peak_memory_stats()
    _t = clk()
    pred = to_full(run_once())
    torch.cuda.synchronize()
    _prop = clk() - _t
    timing["propagate_s"] = round(_prop, 2)
    timing["propagate_ms_per_slice"] = round(_prop * 1000 / D, 1)
    timing["slices_per_s"] = round(D / _prop, 1)
    vram_peak_gib = torch.cuda.max_memory_allocated() / 1024 ** 3
    if not a.no_largest_cc:
        pred = largest_cc(pred)

    metrics = {"case": case, "oar": a.oar, "modality": a.modality, "prompt": a.prompt, "seed_slice": seed_full,
               "grid": list(full_shape), "roi": [D, H, W], "gt_voxels": int(gt_full.sum()),
               "pred_voxels": int(pred.sum()), "dice": round(dice(pred, gt_full), 4)}
    if a.surface:
        metrics.update({k: (round(v, 3) if isinstance(v, float) else v)
                        for k, v in surface_metrics(pred, gt_full,
                                                     grid_img.GetSpacing()).items()})

    # ---- uncertainty (experiment D) ---------------------------------------
    if a.uncertainty > 0:
        acc = np.zeros(full_shape, np.float32)
        for k in range(a.uncertainty):
            rng = np.random.RandomState(1000 + k)
            m = to_full(run_once(a.jitter, rng))
            if not a.no_largest_cc:
                m = largest_cc(m)
            acc += m
            print(f"    uncertainty run {k+1}/{a.uncertainty}  Dice {dice(m, gt_full):.3f}")
        vote = acc / a.uncertainty
        consensus = (vote >= 0.5).astype(np.uint8)
        uimg = sitk.GetImageFromArray((vote * (vote < 1.0) * (vote > 0.0)).astype(np.float32))
        uimg.CopyInformation(grid_img)
        sitk.WriteImage(uimg, os.path.join(out_dir, "uncertainty.nrrd"))
        metrics["consensus_dice"] = round(dice(consensus, gt_full), 4)
        metrics["uncertain_voxels"] = int(((vote > 0) & (vote < 1)).sum())

    # ---- perf / inference estimate ----------------------------------------
    timing["total_s"] = round(clk() - t_start, 2)
    metrics["perf"] = {**timing, "vram_peak_alloc_gib": round(vram_peak_gib, 2),
                       "device": torch.cuda.get_device_name(0), "torch": torch.__version__,
                       "model_size": MODEL_SIZE}

    # ---- outputs ----------------------------------------------------------
    pred_img = sitk.GetImageFromArray(pred.astype(np.uint8)); pred_img.CopyInformation(grid_img)
    sitk.WriteImage(pred_img, os.path.join(out_dir, "pred_mask.nrrd"))
    picks = write_qa(grid_img, mr_vol, pred, gt_full, seed_full, os.path.join(out_dir, "qa.png"))
    json.dump(metrics, open(os.path.join(out_dir, "metrics.json"), "w"), indent=2)

    print(f"\n=== {case}  modality={a.modality}  prompt={a.prompt}  seed z={seed_full} ===")
    for k, v in metrics.items():
        if k == "perf":
            continue
        print(f"  {k:20s} {v}")
    print("  --- perf / inference estimate ---")
    for k, v in metrics["perf"].items():
        print(f"  {k:24s} {v}")
    print(f"  QA slices z={picks} -> {out_dir}/qa.png")
    print(f"  outputs -> {out_dir}/")


if __name__ == "__main__":
    main()
