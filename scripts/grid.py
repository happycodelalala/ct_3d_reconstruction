"""Shared SimpleITK output-grid + registered-resampling core for the dataset builders.

Every CT+MR builder needs the SAME setup: load CT/MR, cached-register MR→CT, build ONE
reference grid from the CT, and resample both volumes into it (so CT, MR, masks and meshes
co-register). That setup lives here — NOT inside a concrete builder — so each builder
(`preprocess_hn_mri`, `build_envelope_dataset`) imports the core instead of reaching into a
sibling builder for it. Pure numpy asset helpers (windowing, meshing, gzip) live one layer
down in `asset_common`; this layer is the SimpleITK grid/registration coupling above it.
"""
import os

import numpy as np
import SimpleITK as sitk

from asset_common import window_u8  # the single window-to-uint8 primitive
from register_ct_mr import register_cached, load_ct_mr  # validated, cached MR->CT registration
from geometry import assert_axis_aligned

OUT_XY = 256               # in-plane output resolution
OUT_Z_CAP = 220            # cap on output slices
CT_HU_LO, CT_HU_HI = -200, 400   # soft-tissue storage window (H&N); app re-windows on top


# ------------------------------------------------------------------ resampling
def output_grid(ct):
    """A reference grid sharing the CT's physical space, downsampled to
    OUT_XY x OUT_XY x min(Z, cap). Everything is resampled into this grid so the
    CT, MR, mask and mesh all register. The grid inherits the CT's origin/direction, so
    guard that the CT is axis-aligned — the front-end renders this grid as an index cube
    and cannot represent oblique cosines (load_ct_mr canonicalizes, so this is a backstop
    for any caller that hands in a raw CT)."""
    assert_axis_aligned(ct, "CT reference grid")
    sz, sp = ct.GetSize(), ct.GetSpacing()
    oz = int(min(sz[2], OUT_Z_CAP))
    out_size = [OUT_XY, OUT_XY, oz]
    out_spacing = [sp[i] * sz[i] / out_size[i] for i in range(3)]
    ref = sitk.Image(out_size, sitk.sitkFloat32)
    ref.SetSpacing(out_spacing)
    ref.SetOrigin(ct.GetOrigin())
    ref.SetDirection(ct.GetDirection())
    return ref, out_size, out_spacing


def to_zyx(img):
    """SimpleITK image -> numpy (Z, Y, X), which ravels (C-order) to the frontend's
    x-fastest, then y, then slice layout: idx = x + X*(y + Y*z)."""
    return sitk.GetArrayFromImage(img)


def window_ct_u8(a):
    return window_u8(a, CT_HU_LO, CT_HU_HI)


def window_mr_u8(a):
    """MR has no standard units; robust-window the non-zero voxels (2..99.5th pct)
    to fill 0..255, so the app's window/level slider has useful range on top."""
    nz = a[a > 0]
    lo, hi = (np.percentile(nz, 2), np.percentile(nz, 99.5)) if nz.size else (0.0, 1.0)
    hi = max(float(hi), float(lo) + 1.0)
    return window_u8(a, lo, hi)


# --------------------------------------------------------------------- emit
def prepare_output_volumes(case_dir):
    """Load CT+MR, cached-register MR->CT, and resample both onto the shared output grid.
    Returns (ref, out_size, out_spacing, ct_hu, ct_u8, mr_u8) — the common setup every
    dataset builder needs, so registration + windowing isn't reimplemented per script. The
    MR goes straight into the output grid via the transform (ref shares the CT's physical
    space) — one interpolation, not a second pass through a full-res CT-grid intermediate."""
    ct, mr = load_ct_mr(case_dir)
    tx = register_cached(ct, mr, os.path.basename(os.path.normpath(case_dir)))
    ref, out_size, out_spacing = output_grid(ct)
    ct_hu = to_zyx(sitk.Resample(ct, ref, sitk.Transform(), sitk.sitkLinear, 0.0, sitk.sitkFloat32))
    mr_u8 = window_mr_u8(to_zyx(sitk.Resample(mr, ref, tx, sitk.sitkLinear, 0.0, sitk.sitkFloat32)))
    return ref, out_size, out_spacing, ct_hu, window_ct_u8(ct_hu), mr_u8
