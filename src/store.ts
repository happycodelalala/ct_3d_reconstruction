import { create } from "zustand";
import { loadDataset as fetchDataset, loadIndex as fetchIndex, type DatasetEntry, type RealDataset } from "./lib/dataset";

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
  showTumor: boolean;
  showBody: boolean; // organ envelope (kidney)
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
  showTumor: true,
  showBody: true,
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
      .then((real) =>
        set({
          real,
          loading: false,
          timepoint: 0,
          sliceMax: real.manifest.dims[2] - 1,
          slice: Math.round(real.manifest.dims[2] / 2),
          window: real.manifest.defaultWL.window,
          level: real.manifest.defaultWL.level,
        })
      )
      .catch((e) => set({ loading: false, loadError: String(e?.message || e) }));
  },
  setTimepoint: (t) =>
    set((st) => ({ timepoint: st.real ? Math.max(0, Math.min(st.real.timepoints.length - 1, t)) : 0 })),
  togglePlaying: () => set((s) => ({ playing: !s.playing })),
  setSlice: (s) => set((st) => ({ slice: Math.max(0, Math.min(st.sliceMax, s)) })),
  setWindow: (w) => set({ window: w }),
  setLevel: (l) => set({ level: l }),
  set: (patch) => set(patch),
}));
