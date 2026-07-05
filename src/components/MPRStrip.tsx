import { useEffect, useMemo, useRef } from "react";
import { useStore } from "../store";
import { renderReformat, renderMIP, realSampler, type PlaneKind } from "../lib/mpr";
import { buildLabelStyle, type Manifest } from "../lib/dataset";

const PLANES: { kind: PlaneKind; label: string }[] = [
  { kind: "coronal", label: "CORONAL" },
  { kind: "sagittal", label: "SAGITTAL" },
  { kind: "oblique", label: "OBLIQUE" },
];

interface TPData {
  ct: Uint8Array;
  mri?: Uint8Array;
  seg?: Uint8Array;
  manifest: Manifest;
}

export default function MPRStrip() {
  const obliqueAngle = useStore((s) => s.obliqueAngle);
  const real = useStore((s) => s.real);
  const timepoint = useStore((s) => s.timepoint);
  const tp = real?.timepoints[timepoint];
  // Stable identity (keyed on dataset/timepoint) so the reformat useMemos in the
  // tiles below aren't invalidated by a fresh object every render — same reason as
  // useTP() in Viewer3D.
  const data = useMemo<TPData | null>(
    () => (real && tp ? { ct: tp.ct, mri: tp.mri, seg: tp.seg, manifest: real.manifest } : null),
    [real, tp]
  );
  if (!data) return null;

  return (
    <section className="mpr">
      <header className="panel-head">
        <span className="panel-kicker">REFORMATS · MPR</span>
        <span className="panel-tag">RESLICED FROM VOLUME</span>
      </header>
      <div className="mpr-grid">
        {PLANES.map((p) => (
          <ProjectionTile key={p.kind} kind={p.kind} label={p.label} data={data} />
        ))}
        <MIPTile data={data} />
      </div>
      <div className="mpr-oblique">
        <span className="scrub-label">OBLIQUE θ</span>
        <input
          type="range"
          min={0}
          max={180}
          step={1}
          value={obliqueAngle}
          onChange={(e) => useStore.getState().set({ obliqueAngle: +e.target.value })}
        />
        <span className="mono">{obliqueAngle}°</span>
      </div>
    </section>
  );
}

function ProjectionTile({ kind, label, data }: { kind: PlaneKind; label: string; data: TPData }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const { window, level, labelVisible, crossX, crossY, slice, sliceMax, obliqueAngle, setSlice, set,
    displayModality, fusionAlpha } = useStore();
  const mode = data.mri ? displayModality : "ct";
  const labelStyle = buildLabelStyle(data.manifest, labelVisible);

  const result = useMemo(() => {
    const cross = { x: crossX, y: crossY, z: slice / Math.max(1, sliceMax) };
    const { sampler, ext } = realSampler(data.ct, data.seg, data.manifest, window, level,
      { mri: data.mri, mode, fusion: fusionAlpha });
    return renderReformat(kind, sampler, ext, cross, { angleDeg: obliqueAngle, labelStyle, base: 224 });
  }, [kind, data, window, level, labelVisible, crossX, crossY, slice, sliceMax, obliqueAngle, mode, fusionAlpha]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = result.img.width;
    canvas.height = result.img.height;
    canvas.getContext("2d")!.putImageData(result.img, 0, 0);
  }, [result]);

  const depthPct =
    kind === "coronal" ? crossY * 100 : kind === "sagittal" ? crossX * 100 : (slice / Math.max(1, sliceMax)) * 100;

  const onClick = (e: React.MouseEvent) => {
    const r = wrapRef.current!.getBoundingClientRect();
    const fx = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    const fy = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
    if (kind === "coronal") { set({ crossX: fx }); setSlice(Math.round((1 - fy) * sliceMax)); }
    else if (kind === "sagittal") { set({ crossY: fx }); setSlice(Math.round((1 - fy) * sliceMax)); }
    else setSlice(Math.round((1 - fy) * sliceMax));
  };
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const dir = e.deltaY > 0 ? 1 : -1;
    if (kind === "coronal") set({ crossY: clamp01(crossY + dir * 0.012) });
    else if (kind === "sagittal") set({ crossX: clamp01(crossX + dir * 0.012) });
    else set({ obliqueAngle: (((obliqueAngle + dir * 3) % 180) + 180) % 180 });
  };

  const [cu, cv] = result.crossUV;
  return (
    <div className="proj-tile">
      <div className="proj-label">{label}<span className="proj-depth mono">{depthPct.toFixed(0)}%</span></div>
      <div className="proj-stage" ref={wrapRef} onClick={onClick} onWheel={onWheel} style={{ aspectRatio: String(result.aspect) }}>
        <canvas ref={canvasRef} className="proj-canvas" />
        <div className="xh xh-v" style={{ left: `${cu * 100}%` }} />
        <div className="xh xh-h" style={{ top: `${cv * 100}%` }} />
        <div className="xh-dot" style={{ left: `${cu * 100}%`, top: `${cv * 100}%` }} />
      </div>
    </div>
  );
}

function MIPTile({ data }: { data: TPData }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const { window, level, labelVisible, crossX, slice, sliceMax, setSlice, set,
    displayModality, fusionAlpha } = useStore();
  const mode = data.mri ? displayModality : "ct";
  const labelStyle = buildLabelStyle(data.manifest, labelVisible);

  const result = useMemo(() => {
    const { sampler, ext } = realSampler(data.ct, data.seg, data.manifest, window, level,
      { mri: data.mri, mode, fusion: fusionAlpha });
    return renderMIP(sampler, ext, { x: 0.5, y: 0.5, z: 0.5 }, { labelStyle, base: 224 });
  }, [data, window, level, labelVisible, mode, fusionAlpha]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = result.img.width;
    canvas.height = result.img.height;
    canvas.getContext("2d")!.putImageData(result.img, 0, 0);
  }, [result]);

  const cu = crossX;
  const cv = 1 - slice / Math.max(1, sliceMax);
  const onClick = (e: React.MouseEvent) => {
    const r = wrapRef.current!.getBoundingClientRect();
    const fx = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    const fy = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
    set({ crossX: fx });
    setSlice(Math.round((1 - fy) * sliceMax));
  };
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setSlice(slice + (e.deltaY > 0 ? 1 : -1));
  };

  return (
    <div className="proj-tile">
      <div className="proj-label">MIP · CORONAL<span className="proj-depth mono">MAX</span></div>
      <div className="proj-stage" ref={wrapRef} onClick={onClick} onWheel={onWheel} style={{ aspectRatio: String(result.aspect) }}>
        <canvas ref={canvasRef} className="proj-canvas" />
        <div className="xh xh-v" style={{ left: `${cu * 100}%` }} />
        <div className="xh xh-h" style={{ top: `${cv * 100}%` }} />
        <div className="xh-dot" style={{ left: `${cu * 100}%`, top: `${cv * 100}%` }} />
      </div>
    </div>
  );
}

function clamp01(v: number) {
  return Math.max(0, Math.min(1, v));
}
