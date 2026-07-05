import { create } from "zustand";
import { loadDataset as fetchDataset, loadIndex as fetchIndex, type DatasetEntry, type DisplayMode, type RealDataset } from "./lib/dataset";

interface AppState {
  datasets: DatasetEntry[];
  indexLoaded: boolean;
  datasetId: string;
  real: RealDataset | null;
  loading: boolean;
  loadError: string | null;

  timepoint: number; // index into real.timepoints
  playing: boolean;
  slice: number; // axial depth (also crosshair Z, as an index)
  sliceMax: number;
  crossX: number;
  crossY: number;
  obliqueAngle: number;
  window: number;
  level: number;
  displayModality: DisplayMode; // ct | mri | fusion (mri/fusion only when tp.mri present)
  fusionAlpha: number; // 0 = all CT, 1 = all MR (fusion mode)
  labelVisible: Record<number, boolean>; // per seg-label visibility (2D overlay + 3D mesh)
  showLayers: boolean;
  showCutPlane: boolean;
  showMPRPlanes: boolean;
  autoRotate: boolean;

  loadIndex: () => void;
  loadDataset: (id: string) => void;
  setTimepoint: (t: number) => void;
  togglePlaying: () => void;
  setSlice: (s: number) => void;
  setWindow: (w: number) => void;
  setLevel: (l: number) => void;
  setDisplayMode: (m: DisplayMode) => void;
  set: (patch: Partial<AppState>) => void;
}

export const useStore = create<AppState>((set, get) => ({
  datasets: [],
  indexLoaded: false,
  datasetId: "",
  real: null,
  loading: false,
  loadError: null,

  timepoint: 0,
  playing: false,
  slice: 0,
  sliceMax: 0,
  crossX: 0.5,
  crossY: 0.5,
  obliqueAngle: 35,
  window: 0.85,
  level: 0.5,
  displayModality: "ct",
  fusionAlpha: 0.5,
  labelVisible: {},
  showLayers: false,
  showCutPlane: true,
  showMPRPlanes: false,
  autoRotate: true,

  loadIndex: () => {
    fetchIndex()
      .then((datasets) => {
        set({ datasets, indexLoaded: true });
        if (datasets.length) get().loadDataset(datasets[0].id);
        else set({ loadError: "no datasets in index — run `npm run data:index`" });
      })
      .catch((e) => set({ indexLoaded: true, loadError: String(e?.message || e) }));
  },
  loadDataset: (id) => {
    if (get().loading) return;
    const entry = get().datasets.find((d) => d.id === id);
    if (!entry) {
      set({ loadError: `unknown dataset ${id}` });
      return;
    }
    set({ datasetId: id, loading: true, loadError: null, real: null, playing: false });
    fetchDataset(entry.base)
      .then((real) => {
        // seed per-label visibility (all on) from the new dataset's labels
        const labelVisible: Record<number, boolean> = {};
        for (const k of Object.keys(real.manifest.labels ?? {})) labelVisible[Number(k)] = true;
        set({
          real,
          loading: false,
          timepoint: 0,
          displayModality: "ct", // reset — new dataset may not have an MR
          labelVisible,
          sliceMax: real.manifest.dims[2] - 1,
          slice: Math.round(real.manifest.dims[2] / 2),
          window: real.manifest.defaultWL.window,
          level: real.manifest.defaultWL.level,
        });
      })
      .catch((e) => set({ loading: false, loadError: String(e?.message || e) }));
  },
  setTimepoint: (t) =>
    set((st) => ({ timepoint: st.real ? Math.max(0, Math.min(st.real.timepoints.length - 1, t)) : 0 })),
  togglePlaying: () => set((s) => ({ playing: !s.playing })),
  setSlice: (s) => set((st) => ({ slice: Math.max(0, Math.min(st.sliceMax, s)) })),
  setWindow: (w) => set({ window: w }),
  setLevel: (l) => set({ level: l }),
  // switching modality also loads that modality's default W/L (the MR is stored
  // pre-windowed, so it wants its own level/width).
  setDisplayMode: (m) =>
    set((st) => {
      const wl = m === "mri" ? st.real?.manifest.mriWL ?? st.real?.manifest.defaultWL : st.real?.manifest.defaultWL;
      return { displayModality: m, ...(wl ? { window: wl.window, level: wl.level } : {}) };
    }),
  set: (patch) => set(patch),
}));
