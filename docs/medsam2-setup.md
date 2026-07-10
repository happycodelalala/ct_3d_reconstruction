# MedSAM2 setup & usage (this repo)

How to install [MedSAM2](https://github.com/bowang-lab/MedSAM2) and run the promptable
single-seed → 3D segmentation used by `scripts/medsam2_seed_test.py`. This is the concrete
runbook behind the [seed-test plan](medsam2-seed-test-plan.md) and the §4.5 recipe in
[ct-mri-tumour-segmentation.md](ct-mri-tumour-segmentation.md); read those for *why*.

**Verified once** on: RTX 3080 Laptop (16 GB, compute 8.6), driver 610.43.02 / CUDA 13.3,
Manjaro, `uv` 0.11.26 → Python 3.12.13, `torch` 2.11.0+cu128, `MedSAM2_latest.pt`.

> A CUDA GPU is required in practice. VRAM is not the constraint — inference peaks ≈2.2 GB;
> the model runs slice-wise.

---

## 1. Python environment

MedSAM2 / SAM2 need **Python ≥ 3.10** — a system 3.9 won't do. `uv` pins a managed 3.12 so
you don't touch the system interpreter.

```bash
# install uv if you don't have it (adds ~/.local/bin/uv)
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

cd /path/to/ct_3d_reconstruction
uv venv --python 3.12                      # creates .venv (gitignored)
```

## 2. PyTorch (CUDA wheel)

Pick the CUDA wheel index matching your setup; **cu128 runs fine on newer drivers**
(drivers are forward-compatible, so a CUDA-13 driver runs a cu128 wheel).

```bash
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
# sanity check — must print True and your GPU name
.venv/bin/python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Runtime deps for the harness + the registration/preprocess scripts:

```bash
uv pip install matplotlib scikit-image pandas hydra-core iopath SimpleITK pillow numpy tqdm
```

## 3. MedSAM2 code + checkpoint

**No `pip install -e .` is needed.** `sam2/__init__.py` self-registers its Hydra config
module on import, so the package is usable directly from a clone. `medsam2_seed_test.py`
adds `MedSAM2/` to `sys.path` itself — you don't even need to set `PYTHONPATH`.

```bash
# clone into the repo root (MedSAM2/ and checkpoints/ are gitignored)
git clone --depth 1 https://github.com/bowang-lab/MedSAM2.git

# download the general checkpoint (~149 MB) used by the harness default
curl -L -o MedSAM2/checkpoints/MedSAM2_latest.pt \
  "https://huggingface.co/wanglab/MedSAM2/resolve/main/MedSAM2_latest.pt"
```

Defaults the harness uses: checkpoint `MedSAM2/checkpoints/MedSAM2_latest.pt`, config
`configs/sam2.1_hiera_t512.yaml` (a **512²** tiny-Hiera model — not 1024²). Override with
`--checkpoint` / `--cfg`.

> **Harmless warning:** on first run you may see `cudaErrorNoKernelImageForDevice` /
> "Skipping the post-processing step". MedSAM2 ships a prebuilt `sam2/_C.so` (a connected-
> components CUDA op) compiled for other GPU archs; it has no kernel for some cards and is
> skipped. It does **not** affect results — the harness does connected-component cleanup with
> scikit-image instead.

## 4. Data (HaN-Seg `case_01`)

The seed test validates the recipe against a real 3D label — HaN-Seg's mandible OAR. Download
is license-gated to **local use only** (see licensing note below).

```bash
mkdir -p hanseg_data
curl -L "https://zenodo.org/records/7442914/files/HaN-Seg.zip?download=1" -o hanseg_data/HaN-Seg.zip
.venv/bin/python -c "import zipfile; zipfile.ZipFile('hanseg_data/HaN-Seg.zip').extractall('hanseg_data')"
# you only need one case for the seed test: hanseg_data/HaN-Seg/set_1/case_01/
```

## 5. Run the seed test

The first run registers MR→CT (~5 min, CPU) and caches the transform to
`hanseg_data/registration_check/<case>/transform.tfm`; every later run reuses it in ~1 s.

```bash
# Best-case ceiling: mask seed on CT, tight ROI crop  →  Dice ≈0.89 on case_01
.venv/bin/python scripts/medsam2_seed_test.py \
  --case-dir hanseg_data/HaN-Seg/set_1/case_01 \
  --modality ct --prompt mask --crop-margin-mm 6 --surface
```

Key flags (`--help` for all):

| Flag | Meaning |
|---|---|
| `--case-dir DIR` | raw HaN-Seg case → register MR→CT inline (cached) |
| `--mr FILE --mask FILE` | fast path: pre-registered MR + a mask (skip registration) |
| `--modality {mr,ct}` | volume to propagate through (`mr` = the recipe; `ct` = mechanism check) |
| `--oar NAME` | target OAR (substring of its `.seg.nrrd`), e.g. `Brainstem`, `Parotid_L`; default `Bone_Mandible`. Soft-tissue OARs are the fair MR test |
| `--prompt {mask,box}` | seed prompt type (mask ≥ box in practice) |
| `--crop-margin-mm N` | ROI crop margin — **first-order** knob; tight (≈6) curbs drift, loose over-segments |
| `--seed-slice N` | force an off-centre seed (drift test); default = largest-area slice |
| `--surface` | also compute ASSD / HD95 / surface-Dice |
| `--no-crop` | feed the whole volume (reproduces the over-segmentation failure) |

The three experiments (experiment D — jittered-prompt uncertainty — was disproven as a confidence
signal and removed; design + findings in [tumour-triage-pipeline.md §4](tumour-triage-pipeline.md)):

```bash
# A  ceiling         mask, centre seed, tight crop
.venv/bin/python scripts/medsam2_seed_test.py --case-dir hanseg_data/HaN-Seg/set_1/case_01 --modality ct --prompt mask --crop-margin-mm 6
# B  mask-vs-box gap
.venv/bin/python scripts/medsam2_seed_test.py --case-dir hanseg_data/HaN-Seg/set_1/case_01 --modality ct --prompt box  --crop-margin-mm 6
# C  drift from an off-centre seed
.venv/bin/python scripts/medsam2_seed_test.py --case-dir hanseg_data/HaN-Seg/set_1/case_01 --modality ct --prompt mask --seed-slice 120
```

**Outputs** land in `runs/medsam2_seed/<case>_<modality>_<prompt>/` (gitignored):
`pred_mask.nrrd` (mask on the CT grid), `qa.png` (pred = red, GT = green contours),
`metrics.json` (Dice + surface + a `perf` block: per-slice time, VRAM, timings).
Metrics also print to the console.

## 6. View a prediction in the workstation

`preprocess_hn_mri.py` turns any mask NRRD into a front-end dataset. Point its `--roi-glob`
at a prediction to *see* the model output (it shares the CT's physical space, so it drops in):

```bash
PRED=$(readlink -f runs/medsam2_seed/case_01_ct_mask/pred_mask.nrrd)
.venv/bin/python scripts/preprocess_hn_mri.py \
  --case-dir hanseg_data/HaN-Seg/set_1/case_01 \
  --id hanseg_case_01_medsam2 --roi-label "MedSAM2 mandible" --roi-glob "$PRED"
npm run data:index        # add it to the picker
npm run dev               # http://localhost:5173
```

Omit `--roi-glob` to build the ground-truth mandible dataset for side-by-side comparison.
These builds reuse the cached transform, so they finish in seconds.

## Performance (case_01, RTX 3080)

Propagation is **~15–27 ms/slice, ≤2.2 GB VRAM**, ~2–4 s for a whole volume. The only slow
step is CT↔MR registration (~5 min, CPU), cached after the first run. **A cohort is
registration-bound, not GPU-bound.**

## Troubleshooting

- **`nvidia-smi` fails / `torch.cuda.is_available()` is False** — the kernel module isn't
  installed for the running kernel. On Manjaro/Arch: `sudo pacman -S linuxXYZ-nvidia`
  (match `uname -r`), reboot, confirm `nvidia_uvm` in `lsmod`.
- **`ModuleNotFoundError: sam2`** — you're running a script *other* than `medsam2_seed_test.py`
  (which self-adds the path). Set `PYTHONPATH=$PWD/MedSAM2` for ad-hoc scripts.
- **`cudaErrorNoKernelImageForDevice`** — harmless, see §3.
- **`python_requires >= 3.10`** on install — you're on the system Python; use `.venv` (§1).

## Licensing

HaN-Seg is **CC-BY-NC-ND** — keep raw *and* derived assets local. `hanseg_data/`,
`public/data/`, `runs/`, `MedSAM2/`, and `checkpoints/` are all gitignored. Do not
redistribute processed assets or checkpoints.
