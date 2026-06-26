/* Build the dataset INDEX that the workstation loads on startup.
 *
 *   node scripts/build_index.cjs      (or: npm run data:index)
 *
 * Scans public/data/<id>/manifest.json for every dataset and emits a single
 * lightweight public/data/index.json the front-end can fetch once to populate
 * the patient picker WITHOUT downloading any volumes. This is how the app scales
 * to hundreds/thousands of patients: add a dataset folder (+ its manifest), then
 * rebuild the index — no code changes.
 *
 * Each index entry carries only what the picker needs to list/search/badge a
 * patient; the heavy assets are fetched lazily when a patient is selected.
 */
const fs = require("fs");
const path = require("path");

const DATA = path.join(__dirname, "..", "public", "data");
const OUT = path.join(DATA, "index.json");

function buildEntry(id) {
  const mfPath = path.join(DATA, id, "manifest.json");
  if (!fs.existsSync(mfPath)) return null;
  let m;
  try {
    m = JSON.parse(fs.readFileSync(mfPath, "utf8"));
  } catch (e) {
    console.warn(`  ! skip ${id}: unreadable manifest (${e.message})`);
    return null;
  }
  const labels = m.labels || {};
  const tps = Array.isArray(m.timepoints) ? m.timepoints : [];
  const hasTumor = !!labels["2"] || tps.some((tp) => tp.tumorMesh);
  return {
    id: m.id || id,
    base: `/data/${id}`,
    title: m.title || id,
    organ: labels["1"] || null,
    modality: m.modality || "—",
    source: m.source || null,
    timepoints: tps.length,
    hasSegmentation: !!m.hasSegmentation,
    hasTumor,
  };
}

function build() {
  if (!fs.existsSync(DATA)) {
    console.error(`no data dir at ${DATA}`);
    process.exit(1);
  }
  const ids = fs
    .readdirSync(DATA, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .sort();

  const datasets = [];
  for (const id of ids) {
    const entry = buildEntry(id);
    if (entry) datasets.push(entry);
  }

  const out = { version: 1, generatedFrom: "manifests", count: datasets.length, datasets };
  fs.writeFileSync(OUT, JSON.stringify(out, null, 2));
  console.log(`wrote ${path.relative(path.join(__dirname, ".."), OUT)} — ${datasets.length} datasets`);
}

build();
