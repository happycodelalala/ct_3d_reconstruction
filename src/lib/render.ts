// The 2D pixel/shading kernel: source-luminance selection (CT/MR/fusion), window/level,
// seg-label tinting, air transparency, and the axial slice rasteriser. Shared by the axial
// viewer (CTPanel via renderRealSlice) and the MPR sampler (mpr.ts imports the kernel) so
// windowing and fusion are defined once. Coronal/sagittal/MIP reformatting lives in mpr.ts;
// GPU texture-wrapping in sliceTexture.ts. Colour resolution lives in color.ts.
import { clamp } from "./math";
import type { DisplayMode, Manifest } from "./dataset";
import type { LabelStyle } from "./color";

export const SEG_TINT = 0.55; // overlay tint strength (mix of greyscale toward the label colour)

// Rounded linear interpolation a↔b (t in 0..1) — the integer channel blend used for the
// seg tint (tintPixel) and the MIP tumour tint (mpr.ts).
export function mix(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t);
}

// Blend a greyscale luminance toward a label colour by SEG_TINT (the seg overlay tint).
export function tintPixel(lum: number, c: [number, number, number]): [number, number, number] {
  return [mix(lum, c[0], SEG_TINT), mix(lum, c[1], SEG_TINT), mix(lum, c[2], SEG_TINT)];
}

// Window a 0..1 source luminance to 0..255 by level/width. Shared by the axial renderer
// and the MPR sampler so windowing is defined once.
export function windowLum(v01: number, level: number, window: number): number {
  const lo = level - window / 2;
  const hi = level + window / 2;
  return clamp(Math.round(((v01 - lo) / Math.max(1e-4, hi - lo)) * 255), 0, 255);
}

// Greyscale luminance + seg label -> RGBA, with the label tint and air transparency.
// Shared by renderRealSlice (axial) and renderReformat (MPR).
export function shade(lum: number, label: number, labelStyle: LabelStyle, transparentAir: boolean) {
  let r = lum, g = lum, b = lum, a = 255;
  if (lum <= 2 && transparentAir) a = 0;
  const c = label ? labelStyle[label] : undefined;
  if (c) { [r, g, b] = tintPixel(lum, c); a = 255; }
  return [r, g, b, a] as const;
}

/** The mode a timepoint can actually render: the chosen mode, or "ct" when the
 *  timepoint has no MR ("mri"/"fusion" aren't drawable without it). One rule, shared
 *  by every 2D/3D view instead of re-inlining the CT fallback in each. */
export function effectiveMode(mri: Uint8Array | undefined, mode: DisplayMode): DisplayMode {
  return mri ? mode : "ct";
}

// Per-voxel source luminance (0..1) for the active display mode: CT, MR, or a
// linear CT/MR blend (fusion). Shared by the axial renderer and the MPR sampler
// so every view fuses identically.
export function srcLum01(
  ct: Uint8Array,
  mri: Uint8Array | undefined,
  vi: number,
  mode: DisplayMode = "ct",
  fusion = 0.5
): number {
  if (mode === "mri" && mri) return mri[vi] / 255;
  if (mode === "fusion" && mri) return (ct[vi] / 255) * (1 - fusion) + (mri[vi] / 255) * fusion;
  return ct[vi] / 255;
}

// Render one axial slice (constant Z = k) to ImageData, with optional contrast
// re-windowing on the already-8bit volume(s) and a seg overlay (when seg present).
export function renderRealSlice(
  ct: Uint8Array,
  seg: Uint8Array | undefined,
  m: Manifest,
  k: number,
  opts: {
    window: number; level: number; labelStyle: LabelStyle; transparentAir?: boolean;
    mri?: Uint8Array; mode?: DisplayMode; fusion?: number;
  }
): ImageData {
  const [X, Y] = m.dims;
  const img = new ImageData(X, Y);
  const data = img.data;

  for (let oy = 0; oy < Y; oy++) {
    const cy = Y - 1 - oy; // flip so +Y is up
    for (let ox = 0; ox < X; ox++) {
      const vi = ox + X * (oy + Y * k);
      const lum = windowLum(srcLum01(ct, opts.mri, vi, opts.mode, opts.fusion), opts.level, opts.window);
      const [r, g, b, a] = shade(lum, seg ? seg[vi] : 0, opts.labelStyle, !!opts.transparentAir);
      const di = (cy * X + ox) * 4;
      data[di] = r; data[di + 1] = g; data[di + 2] = b; data[di + 3] = a;
    }
  }
  return img;
}
