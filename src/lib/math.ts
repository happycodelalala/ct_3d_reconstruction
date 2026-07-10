// Numeric primitives with no dependencies — the leaf every layer can import without
// reaching "up" into the loader. Keeps the render kernel free of a runtime dependency
// on dataset.ts (which would otherwise be only for a clamp).

// Clamp v to [lo, hi] — the one clamp primitive (slice/timepoint indices, 0..1
// fractions, 0..255 channels) instead of re-inlining Math.max(lo, Math.min(hi, …)).
export function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

// Clamp to the unit interval (crosshair positions, click coords, UV).
export function clamp01(v: number): number {
  return clamp(v, 0, 1);
}
