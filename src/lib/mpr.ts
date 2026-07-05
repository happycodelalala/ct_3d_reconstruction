// Multi-planar reformatting (MPR): reslice ONE volume into coronal / sagittal /
// oblique projections, plus a maximum-intensity projection (MIP), via a common
// world-space sampler over the loaded volume.

import { srcLum01, mix, windowLum, shade, type DisplayMode, type LabelStyle, type Manifest } from "./dataset";

export type PlaneKind = "coronal" | "sagittal" | "oblique";

// sample the volume at a world point -> { lum:0..255, label }
export type Sampler = (wx: number, wy: number, wz: number) => { lum: number; label: number };

// ---- real volume (one timepoint of a dataset) ----
export function realSampler(
  ct: Uint8Array,
  seg: Uint8Array | undefined,
  manifest: Manifest,
  window: number,
  level: number,
  disp?: { mri?: Uint8Array; mode?: DisplayMode; fusion?: number }
): { sampler: Sampler; ext: [number, number, number] } {
  const [X, Y, Z] = manifest.dims;
  const ext = manifest.worldExtent;
  const mri = disp?.mri, mode = disp?.mode, fusion = disp?.fusion;
  const sampler: Sampler = (wx, wy, wz) => {
    const ox = Math.round(((wx / (2 * ext[0])) + 0.5) * (X - 1));
    const oy = Math.round(((wy / (2 * ext[1])) + 0.5) * (Y - 1));
    const k = Math.round(((wz / (2 * ext[2])) + 0.5) * (Z - 1));
    if (ox < 0 || oy < 0 || k < 0 || ox >= X || oy >= Y || k >= Z)
      return { lum: 6, label: 0 };
    const vi = ox + X * (oy + Y * k);
    return { lum: windowLum(srcLum01(ct, mri, vi, mode, fusion), level, window), label: seg ? seg[vi] : 0 };
  };
  return { sampler, ext };
}

// ---------------------------------------------------------------------------
export interface PlaneBasis {
  C: [number, number, number]; // plane centre (world)
  U: [number, number, number]; // in-plane horizontal unit dir
  uExt: number; // half-width along U (world)
  V: [number, number, number]; // in-plane vertical unit dir
  vExt: number; // half-height along V (world)
  cu: number; // crosshair offset along U (world)
  cv: number; // crosshair offset along V (world)
}

// Geometry of a reslice plane in shared world space (used by both the 2D
// reformat renderer and the 3D intersecting cut-planes).
export function planeBasis(
  kind: PlaneKind,
  ext: [number, number, number],
  cross: { x: number; y: number; z: number },
  angleDeg: number
): PlaneBasis {
  const [ex, ey, ez] = ext;
  const cwx = (cross.x * 2 - 1) * ex;
  const cwy = (cross.y * 2 - 1) * ey;
  const cwz = (cross.z * 2 - 1) * ez;

  if (kind === "coronal")
    return { C: [0, cwy, 0], U: [1, 0, 0], uExt: ex, V: [0, 0, 1], vExt: ez, cu: cwx, cv: cwz };
  if (kind === "sagittal")
    return { C: [cwx, 0, 0], U: [0, 1, 0], uExt: ey, V: [0, 0, 1], vExt: ez, cu: cwy, cv: cwz };
  // oblique: vertical plane through the crosshair's (x,y), rotated by angle
  const a = (angleDeg * Math.PI) / 180;
  return {
    C: [cwx, cwy, 0],
    U: [Math.cos(a), Math.sin(a), 0],
    uExt: Math.max(ex, ey),
    V: [0, 0, 1],
    vExt: ez,
    cu: 0,
    cv: cwz,
  };
}

export interface ReformatResult {
  img: ImageData;
  aspect: number; // width / height
  crossUV: [number, number]; // crosshair position in 0..1 image coords
  basis: PlaneBasis;
}

export function renderReformat(
  kind: PlaneKind,
  sampler: Sampler,
  ext: [number, number, number],
  cross: { x: number; y: number; z: number },
  opts: { angleDeg: number; labelStyle: LabelStyle; base?: number; transparentAir?: boolean }
): ReformatResult {
  const basis = planeBasis(kind, ext, cross, opts.angleDeg);
  const { C, U, uExt, V, vExt, cu, cv } = basis;
  const base = opts.base ?? 240;
  const aspect = uExt / vExt;
  const W = aspect >= 1 ? base : Math.round(base * aspect);
  const H = aspect >= 1 ? Math.round(base / aspect) : base;

  const img = new ImageData(W, H);
  const d = img.data;
  for (let py = 0; py < H; py++) {
    const v = (0.5 - py / (H - 1)) * 2 * vExt; // top = +V
    for (let px = 0; px < W; px++) {
      const u = (px / (W - 1) - 0.5) * 2 * uExt;
      const s = sampler(C[0] + u * U[0] + v * V[0], C[1] + u * U[1] + v * V[1], C[2] + u * U[2] + v * V[2]);
      const [r, g, b, a] = shade(s.lum, s.label, opts.labelStyle, !!opts.transparentAir);
      const di = (py * W + px) * 4;
      d[di] = r; d[di + 1] = g; d[di + 2] = b; d[di + 3] = a;
    }
  }
  const crossUV: [number, number] = [
    Math.max(0, Math.min(1, cu / (2 * uExt) + 0.5)),
    Math.max(0, Math.min(1, 0.5 - cv / (2 * vExt))),
  ];
  return { img, aspect, crossUV, basis };
}

// Maximum-intensity projection: project along Y (a coronal MIP / "anterior view").
// For each output pixel, march through the depth axis and keep the brightest
// sample; tint amber where the ray crosses tumour voxels.
export function renderMIP(
  sampler: Sampler,
  ext: [number, number, number],
  cross: { x: number; y: number; z: number },
  opts: { labelStyle: LabelStyle; base?: number; steps?: number }
): ReformatResult {
  const [ex, ey, ez] = ext;
  const base = opts.base ?? 240;
  const steps = opts.steps ?? 110;
  const aspect = ex / ez;
  const W = aspect >= 1 ? base : Math.round(base * aspect);
  const H = aspect >= 1 ? Math.round(base / aspect) : base;

  const img = new ImageData(W, H);
  const d = img.data;
  for (let py = 0; py < H; py++) {
    const wz = (0.5 - py / (H - 1)) * 2 * ez;
    for (let px = 0; px < W; px++) {
      const wx = (px / (W - 1) - 0.5) * 2 * ex;
      let maxLum = 0;
      let tumorHit = 0;
      for (let i = 0; i < steps; i++) {
        const wy = (-1 + (2 * i) / (steps - 1)) * ey;
        const s = sampler(wx, wy, wz);
        if (s.lum > maxLum) maxLum = s.lum;
        if (s.label === 2) tumorHit++;
      }
      let r = maxLum, g = maxLum, b = maxLum;
      const tc = opts.labelStyle[2]; // MIP highlights the tumour label (2) when visible
      if (tc && tumorHit > 0) {
        const t = Math.min(1, tumorHit / 6) * 0.7;
        r = mix(maxLum, tc[0], t); g = mix(maxLum, tc[1], t); b = mix(maxLum, tc[2], t * 0.9);
      }
      const di = (py * W + px) * 4;
      d[di] = r; d[di + 1] = g; d[di + 2] = b; d[di + 3] = 255;
    }
  }
  const basis = planeBasis("coronal", ext, cross, 0);
  const crossUV: [number, number] = [
    Math.max(0, Math.min(1, cross.x)),
    Math.max(0, Math.min(1, 1 - cross.z)),
  ];
  return { img, aspect, crossUV, basis };
}
