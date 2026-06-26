import { useEffect } from "react";
import { useStore } from "../store";

// Markers are centered on their position (translateX(-50%)), so end markers at
// 0%/100% would overhang the track — the first colliding with the play button.
// Inset the usable range; --mk-inset (defined on .track) is the single source of
// truth for that inset, shared with the .track-line CSS.
const markerLeft = (frac: number) =>
  `calc(var(--mk-inset) + ${frac} * (100% - 2 * var(--mk-inset)))`;

export default function Timeline() {
  const { real, timepoint, setTimepoint, playing, togglePlaying } = useStore();
  const tps = real?.timepoints ?? [];

  useEffect(() => {
    if (!playing || tps.length < 2) return;
    const id = setInterval(() => {
      const s = useStore.getState();
      s.setTimepoint((s.timepoint + 1) % s.real!.timepoints.length);
    }, 1300);
    return () => clearInterval(id);
  }, [playing, tps.length]);

  if (tps.length < 2) {
    // single acquisition — static marker
    return (
      <div className="timeline">
        <button className="play-btn" disabled style={{ opacity: 0.4, cursor: "default" }}>▶</button>
        <div className="track">
          <div className="track-line" />
          <button className="tp active" style={{ left: markerLeft(0) }}>
            <span className="dot" />
            <span className="tp-label">{tps[0]?.label ?? "—"}</span>
            <span className="tp-day">SINGLE ACQ</span>
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="timeline">
      <button
        className={`play-btn ${playing ? "on" : ""}`}
        onClick={togglePlaying}
        aria-label={playing ? "Pause time series" : "Play time series"}
      >
        {playing ? "❚❚" : "▶"}
      </button>
      <div className="track">
        <div className="track-line" />
        {tps.map((tp, i) => (
            <button
              key={tp.id}
              className={`tp ${i === timepoint ? "active" : ""}`}
              style={{ left: markerLeft(i / (tps.length - 1)) }}
              onClick={() => setTimepoint(i)}
            >
              <span className="dot" />
              <span className="tp-label">{tp.label}</span>
              <span className="tp-day">T{i}</span>
            </button>
        ))}
      </div>
    </div>
  );
}
