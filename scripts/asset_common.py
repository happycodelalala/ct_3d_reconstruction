"""Dependency-light asset helpers shared by every dataset builder.

Pure numpy + scikit-image — **no SimpleITK**. These live here, not in
preprocess_hn_mri (which is registration/SimpleITK-coupled), so the numpy-based
builders (preprocess_hn) can share them without pulling in the whole registration
stack. Extracting them removes the verbatim `mesh_from_mask` duplicate and the
repeated window/gzip patterns that previously drifted between builders.
"""
import gzip

import numpy as np
from skimage import measure
from skimage.filters import gaussian


def window_u8(a, lo, hi):
    """Linear-window a float array to uint8 0..255 over the HU range [lo, hi]."""
    return np.clip((a - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)


def write_gz(path, arr):
    """Gzip (level 6) a uint8 volume as raw x-fastest bytes -> path. mtime=0 keeps the
    output deterministic (default gzip embeds the build time), so identical data yields
    identical bytes across rebuilds — reproducible assets, trivial 'did it change?' diffs."""
    with open(path, "wb") as f:
        f.write(gzip.compress(arr.reshape(-1).tobytes(), 6, mtime=0))


def mesh_from_mask(mask_zyx, ext, sigma=0.6, step=1):
    """Marching-cubes isosurface, vertices mapped into the shared [-ext, +ext] world
    space (index-normalized, so mesh and slices register). Returns
    (mesh_dict, n_vertices, n_faces)."""
    sm = gaussian(mask_zyx.astype(np.float32), sigma=sigma)
    v, fc, _, _ = measure.marching_cubes(sm, level=0.5, step_size=step)
    Zd, Yd, Xd = mask_zyx.shape
    ox = ((v[:, 2] / (Xd - 1)) - 0.5) * 2 * ext[0]
    oy = ((v[:, 1] / (Yd - 1)) - 0.5) * 2 * ext[1]
    oz = ((v[:, 0] / (Zd - 1)) - 0.5) * 2 * ext[2]
    return ({"positions": np.stack([ox, oy, oz], 1).round(4).reshape(-1).tolist(),
             "indices": fc.astype(np.int32).reshape(-1).tolist()}, len(v), len(fc))
