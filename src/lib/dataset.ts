// Loads preprocessed datasets (KiTS, NLST, …) and renders real axial slices.
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
  defaultWL: { window: number; level: number };
  hasSegmentation: boolean;
  labels: Record<string, string> | null;
  clinicalNote?: string;
  timepoints: {
    id: string;
    label: string;
    ct: string;
    seg?: string;
    tumorMesh?: string;
    organMesh?: string;
    lungVolumeCm3?: number;
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
  seg?: Uint8Array;
  tumorMesh?: MeshData;
  organMesh?: MeshData;
  lungVolumeCm3?: number;
}

export interface RealDataset {
  manifest: Manifest;
  timepoints: Timepoint[];
  metrics?: RealMetrics;
}

export interface DatasetEntry {
  id: string;
  base: string;
  short: string; // switcher label
}

export const DATASETS: DatasetEntry[] = [
  { id: "kits_case00000", base: "/data/kits_case00000", short: "KiTS19 · KIDNEY" },
  { id: "nlst_104221", base: "/data/nlst_104221", short: "NLST · LUNG (×3)" },
  { id: "nlst_100012", base: "/data/nlst_100012", short: "NLST · TUMOUR (×2)" },
];

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

async function fetchMesh(url: string): Promise<MeshData> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const j = await res.json();
  return { positions: Float32Array.from(j.positions), indices: Uint32Array.from(j.indices) };
}

export async function loadDataset(id: string): Promise<RealDataset> {
  const entry = DATASETS.find((d) => d.id === id);
  if (!entry) throw new Error(`unknown dataset ${id}`);
  const base = entry.base;
  const manifest = (await fetch(`${base}/manifest.json`).then((r) => r.json())) as Manifest;

  const timepoints = await Promise.all(
    manifest.timepoints.map(async (tp) => ({
      id: tp.id,
      label: tp.label,
      lungVolumeCm3: tp.lungVolumeCm3,
      ct: await fetchGzBin(`${base}/${tp.ct}`),
      seg: tp.seg ? await fetchGzBin(`${base}/${tp.seg}`) : undefined,
      tumorMesh: tp.tumorMesh ? await fetchMesh(`${base}/${tp.tumorMesh}`) : undefined,
      organMesh: tp.organMesh ? await fetchMesh(`${base}/${tp.organMesh}`) : undefined,
    }))
  );

  const out: RealDataset = { manifest, timepoints };
  if (manifest.metrics) {
    out.metrics = (await fetch(`${base}/${manifest.metrics}`).then((r) => r.json())) as RealMetrics;
  }
  return out;
}

// ---------------------------------------------------------------------------
export function voxelToWorld(v: number, axis: number, m: Manifest): number {
  const dim = m.dims[axis];
  return ((v / (dim - 1)) - 0.5) * 2 * m.worldExtent[axis];
}

export function sliceWorldZ(k: number, m: Manifest): number {
  return voxelToWorld(k, 2, m);
}

// Render one axial slice (constant Z = k) to ImageData, with optional contrast
// re-windowing on the already-8bit CT and a seg overlay (when seg present).
export function renderRealSlice(
  ct: Uint8Array,
  seg: Uint8Array | undefined,
  m: Manifest,
  k: number,
  opts: { window: number; level: number; showTumor: boolean; transparentAir?: boolean }
): ImageData {
  const [X, Y] = m.dims;
  const img = new ImageData(X, Y);
  const data = img.data;
  const lo = opts.level - opts.window / 2;
  const hi = opts.level + opts.window / 2;
  const inv = 1 / Math.max(1e-4, hi - lo);

  for (let oy = 0; oy < Y; oy++) {
    const cy = Y - 1 - oy; // flip so +Y is up
    for (let ox = 0; ox < X; ox++) {
      const vi = ox + X * (oy + Y * k);
      const lum = Math.max(0, Math.min(255, Math.round((ct[vi] / 255 - lo) * inv * 255)));
      const label = seg ? seg[vi] : 0;
      let r = lum, g = lum, b = lum, a = 255;
      if (lum <= 2 && opts.transparentAir) a = 0;
      if (opts.showTumor && label === 2) {
        r = mix(lum, 255, 0.62); g = mix(lum, 176, 0.62); b = mix(lum, 84, 0.4); a = 255;
      } else if (opts.showTumor && label === 1) {
        r = mix(lum, 40, 0.28); g = mix(lum, 110, 0.28); b = mix(lum, 120, 0.28);
      }
      const di = (cy * X + ox) * 4;
      data[di] = r; data[di + 1] = g; data[di + 2] = b; data[di + 3] = a;
    }
  }
  return img;
}

function mix(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t);
}
