import { create } from "zustand";
import { loadDataset, DATASETS, type RealDataset } from "./lib/dataset";

interface AppState {
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

  loadDataset: (id: string) => void;
  setTimepoint: (t: number) => void;
  togglePlaying: () => void;
  setSlice: (s: number) => void;
  setWindow: (w: number) => void;
  setLevel: (l: number) => void;
  set: (patch: Partial<AppState>) => void;
}

export const useStore = create<AppState>((set, get) => ({
  datasetId: DATASETS[0].id,
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

  loadDataset: (id) => {
    if (get().loading) return;
    set({ datasetId: id, loading: true, loadError: null, real: null, playing: false });
    loadDataset(id)
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
