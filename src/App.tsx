import { useEffect } from "react";
import Viewer3D from "./components/Viewer3D";
import CTPanel from "./components/CTPanel";
import MPRStrip from "./components/MPRStrip";
import ControlRail from "./components/ControlRail";
import StatsPanel from "./components/StatsPanel";
import Timeline from "./components/Timeline";
import PatientPicker from "./components/PatientPicker";
import { useStore } from "./store";

export default function App() {
  const { real, loading, loadError, loadIndex } = useStore();

  useEffect(() => {
    loadIndex();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const m = real?.manifest;
  const multi = (real?.timepoints.length ?? 0) > 1;
  const hasTumor = real?.timepoints.some((t) => t.tumorMesh) ?? false;
  const hasOrgan = real?.timepoints.some((t) => t.organMesh) ?? false;
  const organName = (m?.labels?.["1"] ?? "organ").toUpperCase();

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" />
          <span className="brand-name">ONCOVOL</span>
          <span className="brand-sub">3D RECONSTRUCTION WORKSTATION</span>
        </div>

        <PatientPicker />

        <div className="topbar-meta">
          <Meta k="STUDY" v={m?.title ?? "—"} />
          <Meta k="MODALITY" v={m?.modality ?? "—"} />
          <Meta k="SERIES" v={multi ? `${real!.timepoints.length} timepoints` : "single"} />
          <Meta k="STATUS" v={m?.hasSegmentation ? "REAL · SEGMENTED" : real ? "REAL · CT" : "—"} accent />
        </div>
      </header>

      {loadError && <div className="load-error">dataset load failed: {loadError}</div>}

      <main className="grid">
        <ControlRail />

        <section className="viewport">
          <div className="viewport-head">
            <span className="panel-kicker">RECONSTRUCTION · VOLUMETRIC</span>
            <div className="axis-legend">
              {hasTumor && <span><i className="sw amber" /> TUMOUR</span>}
              <span><i className="sw cyan" /> ACTIVE SLICE</span>
              {hasOrgan ? (
                <span><i className="sw teal" /> {organName}</span>
              ) : (
                <span><i className="sw teal" /> VOLUME (MPR)</span>
              )}
            </div>
          </div>
          <div className="viewport-canvas">
            <Viewer3D />
            <div className="scanlines" />
            <div className="vignette" />
            {!real && (
              <div className="viewport-loading">
                {loadError ? "DATASET UNAVAILABLE" : "LOADING VOLUME…"}
              </div>
            )}
          </div>
          <Timeline />
        </section>

        <section className="rightcol">
          <CTPanel />
          <MPRStrip />
          <StatsPanel />
        </section>
      </main>
    </div>
  );
}

function Meta({ k, v, accent }: { k: string; v: string; accent?: boolean }) {
  return (
    <div className="meta">
      <span className="meta-k">{k}</span>
      <span className={`meta-v ${accent ? "accent" : ""}`}>{v}</span>
    </div>
  );
}
