// Label -> colour resolution: the single place that turns a seg label integer into an
// RGB overlay/mesh colour. Data-driven — colours come from the manifest (else a fallback
// palette), never hard-coded per label. See docs/schema.md §6.
import type { Manifest } from "./dataset";

// Per-label overlay colours: fallback palette (RGB 0..255) indexed by (label-1). Label 2 =
// tumour stays amber, label 1 teal — so existing 2-label datasets look unchanged.
export const LABEL_PALETTE: [number, number, number][] = [
  [40, 130, 148],   // 1 teal
  [255, 176, 84],   // 2 amber (tumour)
  [170, 120, 210],  // 3 purple
  [120, 200, 120],  // 4 green
  [230, 120, 150],  // 5 pink
];

// Visible label -> RGB. Absent label = hidden. Built from the manifest + the store's
// per-label visibility so the renderers stay data-driven (no hard-coded label values).
export type LabelStyle = Record<number, [number, number, number]>;

// One place to resolve a label's colour: the manifest's labelColors, else the palette.
export function labelColor(m: Manifest | undefined, label: number): [number, number, number] {
  return m?.labelColors?.[String(label)] ?? LABEL_PALETTE[(label - 1) % LABEL_PALETTE.length];
}

export function buildLabelStyle(m: Manifest | undefined, visible: Record<number, boolean>): LabelStyle {
  const out: LabelStyle = {};
  if (!m?.labels) return out;
  for (const key of Object.keys(m.labels)) {
    const lab = Number(key);
    if (visible[lab] === false) continue; // undefined defaults to visible
    out[lab] = labelColor(m, lab);
  }
  return out;
}
