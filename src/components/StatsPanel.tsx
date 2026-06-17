import { useStore } from "../store";

export default function StatsPanel() {
  const real = useStore((s) => s.real);
  const timepoint = useStore((s) => s.timepoint);
  if (!real) return null;
  const m = real.manifest;
  const curTp = real.timepoints[timepoint];

  // lesion quantification — only when the current timepoint actually has a tumour
  if (real.metrics && curTp?.tumorMesh) {
    const x = real.metrics;
    return (
      <section className="stats">
        <header className="panel-head">
          <span className="panel-kicker">LESION QUANTIFICATION</span>
          <span className="status-pill">MEASURED · REAL</span>
        </header>
        <div className="metric-grid">
          <Metric label="TUMOUR VOL" value={x.tumorVolumeCm3.toFixed(1)} unit="cm³" />
          <Metric label="MAX Ø" value={x.maxDiameterMm.toFixed(1)} unit="mm" />
          <Metric label="MEAN Ø" value={x.meanDiameterMm.toFixed(1)} unit="mm" />
          {x.organVolumeCm3 != null ? (
            <Metric label={`${(x.organLabel ?? "organ").toUpperCase()} VOL`} value={x.organVolumeCm3.toFixed(0)} unit="cm³" />
          ) : (
            <Metric label="MODALITY" value={m.modality.split("·").pop()!.trim()} unit="" />
          )}
          <Metric label="TUMOUR VOX" value={(x.tumorVoxels / 1000).toFixed(1)} unit="k" />
          <Metric label="BBOX" value={x.bboxMm[2].toFixed(0)} unit="mm Z" />
        </div>
        <div className="trend">
          <div className="trend-head"><span>PROVENANCE</span><span className="mono dim">single acquisition</span></div>
          <p className="ct-hint" style={{ margin: "8px 0 0" }}>
            {m.clinicalNote ? `${m.clinicalNote}. ` : ""}
            Measured directly from the {m.title} voxel labels at native
            {" "}{m.spacingMm.map((s) => s.toFixed(2)).join("×")} mm spacing; the mesh is a
            marching-cubes isosurface of the mask.
          </p>
        </div>
      </section>
    );
  }

  // no tumour metrics -> acquisition info (+ organ volume when segmented, e.g. NLST lungs)
  const tp = real.timepoints[timepoint];
  const hasOrgan = real.timepoints.some((t) => t.lungVolumeCm3 != null);
  const organName = (m.labels?.["1"] ?? "ORGAN").toUpperCase();
  return (
    <section className="stats">
      <header className="panel-head">
        <span className="panel-kicker">{hasOrgan ? `${organName} · ACQUISITION` : "ACQUISITION"}</span>
        <span className="status-pill">{real.timepoints.length > 1 ? "LONGITUDINAL" : "REAL"}</span>
      </header>
      <div className="metric-grid">
        {hasOrgan ? (
          <Metric label={`${organName} VOL`} value={`${tp.lungVolumeCm3 ?? "—"}`} unit="cm³" />
        ) : (
          <Metric label="IN-PLANE" value={`${m.dims[0]}`} unit="px" />
        )}
        <Metric label="SCREEN" value={`${timepoint + 1}`} unit={`/ ${real.timepoints.length}`} />
        <Metric label="MODALITY" value={m.modality.split("·").pop()!.trim()} unit="" />
        <Metric label="SLICES" value={`${m.dims[2]}`} unit="" />
        <Metric label="Z STEP" value={m.spacingMm[2].toFixed(1)} unit="mm" />
        <Metric label="TIMEPOINTS" value={`${real.timepoints.length}`} unit="" />
      </div>
      <div className="trend">
        <div className="trend-head"><span>SERIES</span><span className="mono dim">{tp.label}</span></div>
        <p className="ct-hint" style={{ margin: "8px 0 0" }}>
          {hasOrgan
            ? `${organName.toLowerCase()} envelope from TotalSegmentator (DICOM SEG via IDC) — lung volume per screen: ${real.timepoints.map((t) => t.lungVolumeCm3).join(" / ")} cm³. No tumour masks ship with NLST.`
            : `${m.title} — ${real.timepoints.length} screening CTs. No masks ship with this collection.`}
          {" "}Use the timeline to scrub between screening years.
        </p>
      </div>
    </section>
  );
}

function Metric({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}<span className="metric-unit">{unit}</span></div>
    </div>
  );
}
