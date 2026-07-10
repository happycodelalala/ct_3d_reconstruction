// Loads preprocessed datasets (HaN-Seg CT+MRI envelopes) and renders real axial slices.
// A dataset has one or more timepoints (longitudinal) and optional segmentation.
// 2D slices and 3D meshes share the SAME normalized world space (see scripts/).

export interface Manifest {
  id: string;
  title: string;
  source: string;
  modality: string;
  dims: [number, number, number]; // X, Y, Z(=slice axis)
  worldExtent: [number, number, number]; // half-width per axis
  spacingMm: [number, number, number];
  storageWindowHU?: { lo: number; hi: number }; // HU window used to pack the CT to uint8 (provenance; not rendered)
  defaultWL: { window: number; level: number };
  mriWL?: { window: number; level: number }; // default W/L when the MR is shown
  hasSegmentation: boolean;
  labels: Record<string, string> | null;
  labelColors?: Record<string, [number, number, number]>; // label value -> RGB 0..255 (else palette)
  clinicalNote?: string;
  timepoints: {
    id: string;
    label: string;
    ct: string;
    mri?: string; // second volume, registered into the same grid (CT+MRI datasets)
    seg?: string;
    tumorMesh?: string; // legacy single-mesh slots (label 2 / label 1); still honoured
    organMesh?: string;
    meshes?: { label: number; file: string }[]; // one isosurface per seg label
  }[];
  meshes: null;
  metrics: string | null;
}

export interface MeshData {
  positions: Float32Array;
  indices: Uint32Array;
}

export interface RealMetrics {
  tumorVolumeCm3: number;
  maxDiameterMm: number;
  meanDiameterMm: number;
  tumorVoxels: number;
  bboxMm: number[];
  organVolumeCm3?: number;
  organLabel?: string;
}

export interface Timepoint {
  id: string;
  label: string;
  ct: Uint8Array;
  mri?: Uint8Array; // registered second volume, same grid as ct (optional)
  seg?: Uint8Array;
  tumorMesh?: MeshData;
  organMesh?: MeshData;
  meshes?: { label: number; mesh: MeshData }[]; // one isosurface per seg label
}

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

export const SEG_TINT = 0.55; // overlay tint strength (mix of greyscale toward the label colour)

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

// Clamp v to [lo, hi] — the one clamp primitive (slice/timepoint indices, 0..1
// fractions) instead of re-inlining Math.max(lo, Math.min(hi, …)) at each site.
export function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}
// Clamp to the unit interval (crosshair positions, click coords, UV).
export function clamp01(v: number): number {
  return clamp(v, 0, 1);
}

// Blend a greyscale luminance toward a label colour by SEG_TINT (the seg overlay tint).
export function mix(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t);
}

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

// Which volume the 2D/3D renderers draw from. "fusion" blends CT+MR.
export type DisplayMode = "ct" | "mri" | "fusion";

/** The mode a timepoint can actually render: the chosen mode, or "ct" when the
 *  timepoint has no MR ("mri"/"fusion" aren't drawable without it). One rule, shared
 *  by every 2D/3D view instead of re-inlining the CT fallback in each. */
export function effectiveMode(mri: Uint8Array | undefined, mode: DisplayMode): DisplayMode {
  return mri ? mode : "ct";
}

export interface RealDataset {
  manifest: Manifest;
  timepoints: Timepoint[];
  metrics?: RealMetrics;
}

// One entry per patient/dataset in the index. Lightweight metadata only — enough
// to list, search and badge a patient in the picker without fetching any volume.
export interface DatasetEntry {
  id: string;
  base: string;
  title: string;
  organ: string | null;
  modality: string;
  source: string | null;
  timepoints: number;
  hasSegmentation: boolean;
  hasTumor: boolean;
}

export interface DatasetIndex {
  version: number;
  count: number;
  datasets: DatasetEntry[];
}

// Load the index of all available datasets (generated by scripts/build_index.cjs).
// This is the single fetch on startup; volumes load lazily on selection.
export async function loadIndex(): Promise<DatasetEntry[]> {
  const res = await fetch("/data/index.json");
  if (!res.ok) throw new Error(`index.json: HTTP ${res.status} (run \`npm run data:index\`)`);
  const j = (await res.json()) as DatasetIndex;
  if (!Array.isArray(j.datasets)) throw new Error("index.json: malformed (no datasets[])");
  return j.datasets;
}

async function fetchGzBin(url: string): Promise<Uint8Array> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  let u8 = new Uint8Array(await res.arrayBuffer());
  // Vite (and some hosts) transparently inflate via Content-Encoding:gzip;
  // only inflate ourselves if the bytes are still gzip-wrapped (magic 1f 8b).
  if (u8.length >= 2 && u8[0] === 0x1f && u8[1] === 0x8b) {
    const stream = new Blob([u8]).stream().pipeThrough(new DecompressionStream("gzip"));
    u8 = new Uint8Array(await new Response(stream).arrayBuffer());
  }
  return u8;
}

// Fetch + parse JSON with the same HTTP guard the binary/index fetches use, so a
// missing/unavailable asset fails with a clear "HTTP 404" instead of a cryptic JSON
// SyntaxError on the 404 body. (loadIndex keeps its own bespoke message.)
async function fetchJson(url: string): Promise<any> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

async function fetchMesh(url: string): Promise<MeshData> {
  const j = await fetchJson(url);
  if (!Array.isArray(j.positions) || !Array.isArray(j.indices))
    throw new Error(`${url}: malformed mesh — need positions[] and indices[]`);
  return { positions: Float32Array.from(j.positions), indices: Uint32Array.from(j.indices) };
}

// Validate the fields the app dereferences unconditionally, so a malformed manifest
// fails at load with an actionable message instead of a deep `undefined` access later
// (there is no server-side schema check — see docs/schema.md §2).
function validateManifest(m: any, base: string): Manifest {
  const bad = (msg: string): never => {
    throw new Error(`${base}/manifest.json: ${msg} (see docs/schema.md)`);
  };
  if (!m || typeof m !== "object") bad("not a JSON object");
  if (typeof m.modality !== "string") bad("modality must be a string"); // StatsPanel/CTPanel call .split() on it
  if (!Array.isArray(m.dims) || m.dims.length !== 3) bad("dims must be [X, Y, Z]");
  if (!Array.isArray(m.worldExtent) || m.worldExtent.length !== 3) bad("worldExtent must be [x, y, z]");
  if (!Array.isArray(m.spacingMm) || m.spacingMm.length !== 3) bad("spacingMm must be [x, y, z]"); // StatsPanel/CTPanel index + .map()
  if (!m.defaultWL || typeof m.defaultWL.window !== "number" || typeof m.defaultWL.level !== "number")
    bad("defaultWL must be { window, level }");
  if (!Array.isArray(m.timepoints) || m.timepoints.length === 0) bad("timepoints[] must be a non-empty array");
  m.timepoints.forEach((tp: any, i: number) => {
    if (!tp || typeof tp.ct !== "string") bad(`timepoints[${i}].ct must be a volume filename`);
  });
  return m as Manifest;
}

export async function loadDataset(base: string): Promise<RealDataset> {
  const manifest = validateManifest(await fetchJson(`${base}/manifest.json`), base);

  const timepoints = await Promise.all(
    manifest.timepoints.map(async (tp) => ({
      id: tp.id,
      label: tp.label,
      ct: await fetchGzBin(`${base}/${tp.ct}`),
      mri: tp.mri ? await fetchGzBin(`${base}/${tp.mri}`) : undefined,
      seg: tp.seg ? await fetchGzBin(`${base}/${tp.seg}`) : undefined,
      tumorMesh: tp.tumorMesh ? await fetchMesh(`${base}/${tp.tumorMesh}`) : undefined,
      organMesh: tp.organMesh ? await fetchMesh(`${base}/${tp.organMesh}`) : undefined,
      meshes: tp.meshes
        ? await Promise.all(tp.meshes.map(async (mm) => ({ label: mm.label, mesh: await fetchMesh(`${base}/${mm.file}`) })))
        : undefined,
    }))
  );

  const out: RealDataset = { manifest, timepoints };
  if (manifest.metrics) {
    out.metrics = (await fetchJson(`${base}/${manifest.metrics}`)) as RealMetrics;
  }
  return out;
}

// ---------------------------------------------------------------------------
export function voxelToWorld(v: number, axis: number, m: Manifest): number {
  const dim = m.dims[axis];
  // Math.max(1, dim-1) guards a single-slice axis (dim===1) from 0/0=NaN — same
  // single-slice guard as store.ts::sliceFraction; identical for all real data (dim≫1).
  return ((v / Math.max(1, dim - 1)) - 0.5) * 2 * m.worldExtent[axis];
}

export function sliceWorldZ(k: number, m: Manifest): number {
  return voxelToWorld(k, 2, m);
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
