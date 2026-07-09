// End-to-end smoke test for the ONCOVOL workstation.
//
//   npm run e2e            (run `npm run e2e:setup` once first)
//
// Spawns its own Vite dev server, drives the real app in headless Chromium
// (software WebGL via SwiftShader — no GPU needed), and asserts the patient
// index + searchable picker + lazy dataset loading work end-to-end. Captures
// console/page errors and screenshots to e2e_shots/. Exit code 0 = pass.
import { spawn } from "node:child_process";
import { readdirSync, mkdirSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 5199;
const ORIGIN = `http://127.0.0.1:${PORT}`;
const SHOTS = path.join(ROOT, "e2e_shots");
const LIBS = path.join(homedir(), ".cache", "ct3d-e2e", "libs");

let fails = 0;
const ok = (c, m) => { console.log(`${c ? "  ✓" : "  ✗ FAIL"} ${m}`); if (!c) fails++; };
const txt = (page, sel) => page.$eval(sel, (e) => e.textContent.trim()).catch(() => null);

function findChrome() {
  const base = path.join(homedir(), ".cache", "puppeteer", "chrome");
  if (!existsSync(base)) throw new Error("Chromium not found — run `npm run e2e:setup`");
  const ver = readdirSync(base).filter((d) => d.startsWith("linux-")).sort().pop();
  if (!ver) throw new Error("Chromium not found — run `npm run e2e:setup`");
  return path.join(base, ver, "chrome-linux64", "chrome");
}

async function waitForServer(url, ms = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { if ((await fetch(url)).ok) return; } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`dev server did not come up at ${url}`);
}

mkdirSync(SHOTS, { recursive: true });
const chrome = findChrome();
if (!existsSync(path.join(LIBS, "libnss3.so"))) {
  console.warn(`! runtime libs missing at ${LIBS} — if Chromium fails to launch, run \`npm run e2e:setup\``);
}

console.log("starting dev server…");
const server = spawn("npm", ["run", "dev", "--", "--port", String(PORT), "--host", "127.0.0.1"],
  { cwd: ROOT, stdio: "ignore" });
let browser;
try {
  await waitForServer(ORIGIN);

  browser = await puppeteer.launch({
    executablePath: chrome,
    headless: "new",
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--use-gl=swiftshader",
           "--enable-unsafe-swiftshader", "--window-size=1600,1000"],
    env: { ...process.env, LD_LIBRARY_PATH: `${LIBS}:${process.env.LD_LIBRARY_PATH ?? ""}` },
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });

  const errors = [];
  const notFound = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push("PAGEERROR: " + e.message));
  page.on("response", (r) => { if (r.status() === 404) notFound.push(new URL(r.url()).pathname); });

  console.log("\n1) app loads index + auto-loads first dataset");
  // NOTE: not networkidle0 — Vite's HMR WebSocket stays open, so the network
  // never goes idle. domcontentloaded + the explicit picker wait below is right.
  await page.goto(ORIGIN, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForFunction(() => {
    const el = document.querySelector(".picker-label");
    return el && !/LOADING|SELECT|NO PATIENTS/.test(el.textContent);
  }, { timeout: 20000 });
  ok(true, `picker shows current patient: "${await txt(page, ".picker-label")}"`);
  // Count is data-dependent (public/data is gitignored + optional pipelines like
  // HaN-Seg add datasets), so derive the expected number from the index itself.
  const nDatasets = await fetch(`${ORIGIN}/data/index.json`).then((r) => r.json()).then((j) => j.datasets.length).catch(() => 0);
  ok((await txt(page, ".picker-count")) === String(nDatasets), `picker count badge = ${nDatasets} patients`);
  ok(/SEGMENTED|REAL/.test((await txt(page, ".meta-v.accent")) ?? ""), "STATUS meta populated (dataset really loaded)");
  ok(!!(await page.$("canvas")), "3D <canvas> mounted");
  await page.screenshot({ path: path.join(SHOTS, "01_loaded.png") });

  console.log("\n2) open picker → all patients listed");
  await page.click(".picker-trigger");
  await page.waitForSelector(".picker-pop", { timeout: 5000 });
  ok((await page.$$eval(".picker-row", (e) => e.length)) === nDatasets, `popup lists ${nDatasets} patient rows`);
  await page.screenshot({ path: path.join(SHOTS, "02_open.png") });

  console.log("\n3) search filters the list");
  await page.type(".picker-search", "segmentation");
  await page.waitForFunction(() => document.querySelectorAll(".picker-row").length === 1, { timeout: 5000 })
    .then(() => ok(true, '"segmentation" → 1 row'))
    .catch(async () => ok(false, `"segmentation" expected 1, got ${await page.$$eval(".picker-row", (e) => e.length)}`));

  console.log("\n4) selecting a patient lazy-loads it");
  const before = await txt(page, ".picker-label");
  await page.click(".picker-row");
  await page.waitForFunction((prev) => {
    const el = document.querySelector(".picker-label");
    return el && el.textContent.trim() !== prev && !/LOADING/.test(el.textContent);
  }, { timeout: 20000 }, before);
  const after = await txt(page, ".picker-label");
  ok(after !== before && /segmentation/i.test(after), `switched: "${before}" → "${after}"`);
  ok((await page.$(".picker-pop")) === null, "popup closes after selection");
  await page.screenshot({ path: path.join(SHOTS, "03_switched.png") });

  console.log("\n5) GPU / console health (dispose fix; no WebGL context loss)");
  const ctxLost = errors.filter((e) => /context lost|CONTEXT_LOST|out of memory/i.test(e));
  ok(ctxLost.length === 0, `no WebGL context-loss errors (${ctxLost.length})`);
  // /favicon.ico 404 is a browser default (no favicon is declared) — benign.
  const badAssets = [...new Set(notFound)].filter((p) => p !== "/favicon.ico");
  ok(badAssets.length === 0, `no missing app assets${badAssets.length ? " — " + badAssets.join(", ") : " (favicon 404 is benign)"}`);
  // Drop SwiftShader/WebGL software-renderer noise and the favicon 404 console line.
  const real = errors.filter((e) =>
    !/swiftshader|deprecated|Multiple instances|performance|GroupMarker|fallback|GL_/i.test(e) &&
    !/favicon\.ico|Failed to load resource: the server responded with a status of 404/i.test(e));
  if (real.length) { console.log("  console errors:"); real.forEach((e) => console.log("    · " + e.slice(0, 160))); }
  ok(real.length === 0, `no unexpected console/page errors (${real.length})`);
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
}

console.log(`\n${fails === 0 ? "ALL PASS ✅" : `${fails} FAILURE(S) ❌`}  (screenshots: e2e_shots/)`);
process.exit(fails === 0 ? 0 : 1);
