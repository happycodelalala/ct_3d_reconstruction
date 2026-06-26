import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../store";
import type { DatasetEntry } from "../lib/dataset";

// Searchable patient picker. Replaces the top-bar button row so the workstation
// scales to hundreds/thousands of patients: fuzzy-ish substring search across
// id/title/organ/modality/source, capped render for large result sets, and
// keyboard navigation (↑/↓/Enter/Esc) for fast browsing.
const RENDER_CAP = 200;

function match(d: DatasetEntry, q: string): boolean {
  return (
    d.id.toLowerCase().includes(q) ||
    d.title.toLowerCase().includes(q) ||
    (d.organ ?? "").toLowerCase().includes(q) ||
    d.modality.toLowerCase().includes(q) ||
    (d.source ?? "").toLowerCase().includes(q)
  );
}

export default function PatientPicker() {
  const datasets = useStore((s) => s.datasets);
  const datasetId = useStore((s) => s.datasetId);
  const loading = useStore((s) => s.loading);
  const indexLoaded = useStore((s) => s.indexLoaded);
  const loadDataset = useStore((s) => s.loadDataset);

  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const current = datasets.find((d) => d.id === datasetId);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? datasets.filter((d) => match(d, s)) : datasets;
  }, [datasets, q]);
  const shown = filtered.slice(0, RENDER_CAP);

  // reset highlight when the result set changes
  useEffect(() => setActive(0), [q, open]);

  // outside-click + Escape to close; focus the search box on open
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    inputRef.current?.focus();
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  // keep the highlighted row in view
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-i="${active}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const pick = (id: string) => {
    setOpen(false);
    setQ("");
    if (id !== datasetId) loadDataset(id);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") return setOpen(false);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(shown.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const d = shown[active];
      if (d) pick(d.id);
    }
  };

  const label = loading
    ? "LOADING…"
    : current?.title ?? (indexLoaded ? (datasets.length ? "SELECT PATIENT" : "NO PATIENTS") : "LOADING INDEX…");

  return (
    <div className="picker" ref={rootRef}>
      <button
        className={`picker-trigger ${open ? "open" : ""}`}
        onClick={() => setOpen((o) => !o)}
        disabled={!indexLoaded || datasets.length === 0}
      >
        <span className="picker-dot" />
        <span className="picker-label">{label}</span>
        <span className="picker-count mono">{datasets.length ? `${datasets.length}` : ""}</span>
        <span className="picker-caret">▾</span>
      </button>

      {open && (
        <div className="picker-pop" onKeyDown={onKeyDown}>
          <input
            ref={inputRef}
            className="picker-search"
            placeholder={`Search ${datasets.length} patients — id, organ, modality…`}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="picker-list" ref={listRef}>
            {shown.length === 0 && <div className="picker-empty">no matches</div>}
            {shown.map((d, i) => (
              <button
                key={d.id}
                data-i={i}
                className={`picker-row ${d.id === datasetId ? "on" : ""} ${i === active ? "active" : ""}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(d.id)}
              >
                <span className="picker-row-title">{d.title}</span>
                <span className="picker-row-tags">
                  {d.organ && <span className="ptag">{d.organ}</span>}
                  {d.hasTumor && <span className="ptag amber">tumour</span>}
                  {d.timepoints > 1 && <span className="ptag">×{d.timepoints}</span>}
                  <span className="ptag dim">{d.modality}</span>
                </span>
              </button>
            ))}
          </div>
          {filtered.length > RENDER_CAP && (
            <div className="picker-foot mono">
              showing {RENDER_CAP} of {filtered.length} — refine search
            </div>
          )}
        </div>
      )}
    </div>
  );
}
