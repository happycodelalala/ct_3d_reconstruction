import { useEffect, useRef } from "react";
import { useStore } from "../store";
import { buildLabelStyle, renderRealSlice, sliceWorldZ, effectiveMode } from "../lib/dataset";

const SIZE = 360;

export default function CTPanel() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const { slice, window, level, labelVisible, setSlice, sliceMax, real, timepoint, crossX, crossY, set,
    displayModality, fusionAlpha } = useStore();
  const tp = real?.timepoints[timepoint];
  const mode = effectiveMode(tp?.mri, displayModality);
  const labelStyle = buildLabelStyle(real?.manifest, labelVisible);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !real || !tp) return;
    const ctx = canvas.getContext("2d")!;
    const [X, Y] = real.manifest.dims;
    const img = renderRealSlice(tp.ct, tp.seg, real.manifest, slice,
      { window, level, labelStyle, mri: tp.mri, mode, fusion: fusionAlpha });
    const tmp = document.createElement("canvas");
    tmp.width = X; tmp.height = Y;
    tmp.getContext("2d")!.putImageData(img, 0, 0);
    canvas.width = SIZE; canvas.height = SIZE;
    ctx.imageSmoothingEnabled = true;
    ctx.clearRect(0, 0, SIZE, SIZE);
    ctx.drawImage(tmp, 0, 0, SIZE, SIZE);
  }, [slice, window, level, labelVisible, real, tp, mode, fusionAlpha]);

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setSlice(slice + (e.deltaY > 0 ? 1 : -1));
  };
  const onClick = (e: React.MouseEvent) => {
    const r = stageRef.current!.getBoundingClientRect();
    const fx = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    const fy = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
    set({ crossX: fx, crossY: 1 - fy });
  };

  const m = real?.manifest;
  const sliceCount = m ? m.dims[2] : 0;
  const z = m ? sliceWorldZ(slice, m) : 0;
  const recon = m ? `${m.spacingMm[2].toFixed(1)}mm · ${m.modality}` : "—";
  const hasSeg = !!tp?.seg;
  const segOn = hasSeg && Object.values(labelVisible).some(Boolean);
  const segHint = m?.labels ? Object.values(m.labels).join(" · ") : "";

  return (
    <section className="ct-panel" aria-label="Original CT projection">
      <header className="panel-head">
        <span className="panel-kicker">SOURCE · AXIAL {mode === "mri" ? "MR" : mode === "fusion" ? "CT+MR" : "CT"}</span>
        <span className="panel-tag">{recon}</span>
      </header>

      <div className="ct-stage" ref={stageRef} onWheel={onWheel} onClick={onClick}>
        <canvas ref={canvasRef} width={SIZE} height={SIZE} className="ct-canvas" />
        <div className="xh xh-v" style={{ left: `${crossX * 100}%` }} />
        <div className="xh xh-h" style={{ top: `${(1 - crossY) * 100}%` }} />
        <div className="xh-dot" style={{ left: `${crossX * 100}%`, top: `${(1 - crossY) * 100}%` }} />
        <div className="ct-overlay">
          <div className="corner tl">
            <div>ONCOVOL-WS</div>
            <div className="dim">PT· {m?.title ?? "—"}</div>
            <div className="dim">SER· {tp?.label ?? "—"}</div>
          </div>
          <div className="corner tr">
            <div>WL {(level * 100) | 0}</div>
            <div>WW {(window * 100) | 0}</div>
            <div className="dim">REAL</div>
          </div>
          <div className="corner bl">
            <div>IM {slice + 1}/{sliceCount}</div>
            <div className="dim">Z {z.toFixed(3)}</div>
          </div>
          <div className="corner br dim">
            <div>FUSED SEG</div>
            <div>{hasSeg ? (segOn ? "ON" : "OFF") : "N/A"}</div>
          </div>
        </div>
      </div>

      <div className="ct-scrub">
        <label className="scrub-label">SLICE</label>
        <input type="range" min={0} max={Math.max(1, sliceMax)} value={slice} onChange={(e) => setSlice(+e.target.value)} />
        <span className="mono">{String(slice + 1).padStart(3, "0")}</span>
      </div>
      <p className="ct-hint">
        scroll to page slices · click to move the crosshair{hasSeg ? ` · ${segHint}` : ""}
      </p>
    </section>
  );
}
