import { useEffect, useMemo, useRef } from "react";
import { useStore, sliceFraction } from "../store";
import { renderReformat, renderMIP, realSampler, type PlaneKind } from "../lib/mpr";
import { buildLabelStyle, effectiveMode, clamp01, type Manifest } from "../lib/dataset";

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

// Shared tile: draws a rendered ImageData to a canvas + overlays the crosshair. onPick
// gets the click as (fx, fy) fractions in [0,1]; onWheel gets the raw deltaY. The two
// tiles below differ only in how they compute the image and handle picks/wheel.
function ProjCanvas({ img, aspect, cu, cv, label, depth, onPick, onWheel }: {
  img: ImageData; aspect: number; cu: number; cv: number; label: string; depth: string;
  onPick: (fx: number, fy: number) => void; onWheel: (deltaY: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = img.width;
    canvas.height = img.height;
    canvas.getContext("2d")!.putImageData(img, 0, 0);
  }, [img]);
  const handleClick = (e: React.MouseEvent) => {
    const r = wrapRef.current!.getBoundingClientRect();
    onPick(clamp01((e.clientX - r.left) / r.width), clamp01((e.clientY - r.top) / r.height));
  };
  return (
    <div className="proj-tile">
      <div className="proj-label">{label}<span className="proj-depth mono">{depth}</span></div>
      <div className="proj-stage" ref={wrapRef} onClick={handleClick}
        onWheel={(e) => { e.preventDefault(); onWheel(e.deltaY); }} style={{ aspectRatio: String(aspect) }}>
        <canvas ref={canvasRef} className="proj-canvas" />
        <div className="xh xh-v" style={{ left: `${cu * 100}%` }} />
        <div className="xh xh-h" style={{ top: `${cv * 100}%` }} />
        <div className="xh-dot" style={{ left: `${cu * 100}%`, top: `${cv * 100}%` }} />
      </div>
    </div>
  );
}

function ProjectionTile({ kind, label, data }: { kind: PlaneKind; label: string; data: TPData }) {
  const { window, level, labelVisible, crossX, crossY, slice, sliceMax, obliqueAngle, setSlice, set,
    displayModality, fusionAlpha } = useStore();
  const mode = effectiveMode(data.mri, displayModality);
  const labelStyle = buildLabelStyle(data.manifest, labelVisible);

  const result = useMemo(() => {
    const cross = { x: crossX, y: crossY, z: sliceFraction(slice, sliceMax) };
    const { sampler, ext } = realSampler(data.ct, data.seg, data.manifest, window, level,
      { mri: data.mri, mode, fusion: fusionAlpha });
    return renderReformat(kind, sampler, ext, cross, { angleDeg: obliqueAngle, labelStyle, base: 224 });
  }, [kind, data, window, level, labelVisible, crossX, crossY, slice, sliceMax, obliqueAngle, mode, fusionAlpha]);

  const depthPct =
    kind === "coronal" ? crossY * 100 : kind === "sagittal" ? crossX * 100 : sliceFraction(slice, sliceMax) * 100;

  const onPick = (fx: number, fy: number) => {
    if (kind === "coronal") { set({ crossX: fx }); setSlice(Math.round((1 - fy) * sliceMax)); }
    else if (kind === "sagittal") { set({ crossY: fx }); setSlice(Math.round((1 - fy) * sliceMax)); }
    else setSlice(Math.round((1 - fy) * sliceMax));
  };
  const onWheel = (deltaY: number) => {
    const dir = deltaY > 0 ? 1 : -1;
    if (kind === "coronal") set({ crossY: clamp01(crossY + dir * 0.012) });
    else if (kind === "sagittal") set({ crossX: clamp01(crossX + dir * 0.012) });
    else set({ obliqueAngle: (((obliqueAngle + dir * 3) % 180) + 180) % 180 });
  };

  const [cu, cv] = result.crossUV;
  return <ProjCanvas img={result.img} aspect={result.aspect} cu={cu} cv={cv} label={label}
    depth={`${depthPct.toFixed(0)}%`} onPick={onPick} onWheel={onWheel} />;
}

function MIPTile({ data }: { data: TPData }) {
  const { window, level, labelVisible, crossX, slice, sliceMax, setSlice, set,
    displayModality, fusionAlpha } = useStore();
  const mode = effectiveMode(data.mri, displayModality);
  const labelStyle = buildLabelStyle(data.manifest, labelVisible);

  const result = useMemo(() => {
    const { sampler, ext } = realSampler(data.ct, data.seg, data.manifest, window, level,
      { mri: data.mri, mode, fusion: fusionAlpha });
    return renderMIP(sampler, ext, { x: 0.5, y: 0.5, z: 0.5 }, { labelStyle, base: 224 });
  }, [data, window, level, labelVisible, mode, fusionAlpha]);

  const onPick = (fx: number, fy: number) => { set({ crossX: fx }); setSlice(Math.round((1 - fy) * sliceMax)); };
  const onWheel = (deltaY: number) => setSlice(slice + (deltaY > 0 ? 1 : -1));

  return <ProjCanvas img={result.img} aspect={result.aspect} cu={crossX} cv={1 - sliceFraction(slice, sliceMax)}
    label="MIP · CORONAL" depth="MAX" onPick={onPick} onWheel={onWheel} />;
}

