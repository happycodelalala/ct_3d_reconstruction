import { useStore } from "../store";
import { labelColor, type DisplayMode } from "../lib/dataset";

const MODES: { m: DisplayMode; label: string }[] = [
  { m: "ct", label: "CT" },
  { m: "mri", label: "MR" },
  { m: "fusion", label: "FUSION" },
];

function Toggle({
  label,
  active,
  onClick,
  disabled,
  color,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  color?: string;
}) {
  return (
    <button className={`toggle ${active ? "on" : ""}`} onClick={onClick} disabled={disabled}>
      <span className="toggle-dot" style={color && active ? { background: color, boxShadow: `0 0 6px ${color}` } : undefined} />
      {label}
    </button>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  display,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  display: string;
}) {
  return (
    <div className="rail-slider">
      <div className="rail-slider-head">
        <span>{label}</span>
        <span className="mono">{display}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(+e.target.value)}
      />
    </div>
  );
}

export default function ControlRail() {
  const s = useStore();
  const m = s.real?.manifest;
  const tps = s.real?.timepoints ?? [];
  const hasSeg = !!m?.hasSegmentation;
  const hasMri = !!tps[s.timepoint]?.mri;
  // the W/L sliders act on whichever volume is displayed, so the title tracks it
  const winTitle = `${MODES.find((x) => x.m === s.displayModality)?.label ?? "CT"} WINDOWING`;
  return (
    <aside className="rail">
      {hasMri && (
        <div className="rail-group">
          <div className="rail-title">MODALITY</div>
          <div className="seg-row">
            {MODES.map(({ m: mo, label }) => (
              <button
                key={mo}
                className={`seg ${s.displayModality === mo ? "on" : ""}`}
                onClick={() => s.setDisplayMode(mo)}
              >
                {label}
              </button>
            ))}
          </div>
          {s.displayModality === "fusion" && (
            <Slider
              label="CT ↔ MR BLEND"
              value={s.fusionAlpha}
              min={0}
              max={1}
              step={0.05}
              onChange={(v) => s.set({ fusionAlpha: v })}
              display={`${Math.round(s.fusionAlpha * 100)}% MR`}
            />
          )}
        </div>
      )}

      <div className="rail-group">
        <div className="rail-title">RENDER LAYERS</div>
        {hasSeg && m?.labels &&
          Object.entries(m.labels).map(([k, name]) => {
            const lab = Number(k);
            const rgb = labelColor(m, lab);
            const on = s.labelVisible[lab] !== false;
            return (
              <Toggle
                key={k}
                label={name.toUpperCase()}
                color={`rgb(${rgb[0]},${rgb[1]},${rgb[2]})`}
                active={on}
                onClick={() => s.set({ labelVisible: { ...s.labelVisible, [lab]: !on } })}
              />
            );
          })}
        <Toggle label="SYNCED CUT-PLANE" active={s.showCutPlane} onClick={() => s.set({ showCutPlane: !s.showCutPlane })} />
        <Toggle label="MPR ORTHO BOX" active={s.showMPRPlanes} onClick={() => s.set({ showMPRPlanes: !s.showMPRPlanes })} />
        <Toggle label="MULTI-LAYER STACK" active={s.showLayers} onClick={() => s.set({ showLayers: !s.showLayers })} />
        <Toggle label="AUTO-ORBIT" active={s.autoRotate} onClick={() => s.set({ autoRotate: !s.autoRotate })} />
      </div>

      <div className="rail-group">
        <div className="rail-title">{winTitle}</div>
        <Slider
          label="WINDOW WIDTH"
          value={s.window}
          min={0.1}
          max={1}
          step={0.01}
          onChange={s.setWindow}
          display={(s.window * 100).toFixed(0)}
        />
        <Slider
          label="WINDOW LEVEL"
          value={s.level}
          min={0.1}
          max={0.9}
          step={0.01}
          onChange={s.setLevel}
          display={(s.level * 100).toFixed(0)}
        />
      </div>

      <div className="rail-foot">
        <div className="dim mono">DATASET</div>
        <div>{m?.title ?? "—"}</div>
        <div className="dim mono" style={{ marginTop: 6 }}>SEGMENTATION</div>
        <div className="muted">
          {hasSeg
            ? "expert labels · meshes are marching-cubes isosurfaces (no learned model)"
            : "none in this collection · 3D shows the CT volume via reslices"}
        </div>
      </div>
    </aside>
  );
}
