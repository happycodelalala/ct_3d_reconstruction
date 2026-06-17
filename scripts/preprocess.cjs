/* Preprocess a KiTS19 case (CT + kidney/tumour segmentation) into compact,
 * browser-ready assets that share ONE normalized coordinate space so the 2D
 * slices and the 3D meshes line up.
 *
 *   node scripts/preprocess.cjs
 *
 * Outputs to public/data/kits_case00000/:
 *   manifest.json   dims, spacing, world extents, window, slice count
 *   ct.bin.gz       uint8 windowed CT volume   (outX*outY*outZ)
 *   seg.bin.gz      uint8 label volume 0/1/2    (same dims)
 *   tumor.json      { positions:[...], indices:[...] }  isosurface of label 2
 *   kidney.json     isosurface of label 1 (+2)
 *   metrics.json    volumes / diameters from the real voxels
 */
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const nifti = require("nifti-reader-js");
const isosurface = require("isosurface");

const RAW = path.join(__dirname, "..", "rawdata");
const OUT = path.join(__dirname, "..", "public", "data", "kits_case00000");

// CT display resolution and windowing (abdominal soft-tissue window)
const OUT_XY = 256;
const OUT_Z = 240;
const WIN_LEVEL = 50; // HU
const WIN_WIDTH = 400; // HU
// marching-cubes mask resolution (largest axis)
const MASK_MAX = 168;

function loadNifti(file) {
  const buf = fs.readFileSync(file);
  let ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  if (nifti.isCompressed(ab)) ab = nifti.decompress(ab);
  const hdr = nifti.readHeader(ab);
  const imgBuf = nifti.readImage(hdr, ab);
  const [, nx, ny, nz] = hdr.dims;
  let data;
  switch (hdr.datatypeCode) {
    case 2: data = new Uint8Array(imgBuf); break;
    case 256: data = new Int8Array(imgBuf); break;
    case 4: data = new Int16Array(imgBuf); break;
    case 512: data = new Uint16Array(imgBuf); break;
    case 8: data = new Int32Array(imgBuf); break;
    case 16: data = new Float32Array(imgBuf); break;
    case 64: data = new Float64Array(imgBuf); break;
    default: throw new Error("unsupported datatypeCode " + hdr.datatypeCode);
  }
  const slope = hdr.scl_slope || 0;
  const inter = hdr.scl_inter || 0;
  return {
    nx, ny, nz,
    spacing: [Math.abs(hdr.pixDims[1]), Math.abs(hdr.pixDims[2]), Math.abs(hdr.pixDims[3])],
    data,
    rescale: slope !== 0 ? (v) => v * slope + inter : (v) => v,
  };
}

const at = (vol, x, y, z) => vol.data[x + vol.nx * (y + vol.ny * z)];

console.log("loading imaging…");
const img = loadNifti(path.join(RAW, "case_00000_img.nii.gz"));
console.log("loading segmentation…");
const seg = loadNifti(path.join(RAW, "case_00000_seg.nii.gz"));
console.log(`  img dims ${img.nx}x${img.ny}x${img.nz} spacing ${img.spacing.map(s=>s.toFixed(2))}`);
console.log(`  seg dims ${seg.nx}x${seg.ny}x${seg.nz}`);

// Image axis order is (a=axis0 cranio-caudal slice stack, b=axis1, c=axis2 in-plane).
// We re-map to a clean output space: outX<-b, outY<-c, outZ<-a (the slice axis),
// so the front-end pages true axial slices along Z.
const nA = img.nx, nB = img.ny, nC = img.nz; // 611, 512, 512
// physical size (mm) per image axis
const physA = nA * img.spacing[0];
const physB = nB * img.spacing[1];
const physC = nC * img.spacing[2];
const maxHalf = Math.max(physA, physB, physC) / 2;
// half-width per IMAGE axis (a,b,c) in shared normalized space
const extImg = [physA / (2 * maxHalf), physB / (2 * maxHalf), physC / (2 * maxHalf)];
// world extents in OUTPUT order (X<-b, Y<-c, Z<-a)
const worldExtent = [extImg[1], extImg[2], extImg[0]];

// ---------------------------------------------------------------------------
// 1) CT + seg display volumes (nearest-neighbour resample + windowing)
console.log("resampling CT + seg volumes…");
const lo = WIN_LEVEL - WIN_WIDTH / 2;
const hi = WIN_LEVEL + WIN_WIDTH / 2;
const ct = new Uint8Array(OUT_XY * OUT_XY * OUT_Z);
const sg = new Uint8Array(OUT_XY * OUT_XY * OUT_Z);
for (let oz = 0; oz < OUT_Z; oz++) {
  const a = Math.min(nA - 1, Math.round((oz / (OUT_Z - 1)) * (nA - 1)));
  for (let oy = 0; oy < OUT_XY; oy++) {
    const c = Math.min(nC - 1, Math.round((oy / (OUT_XY - 1)) * (nC - 1)));
    for (let ox = 0; ox < OUT_XY; ox++) {
      const b = Math.min(nB - 1, Math.round((ox / (OUT_XY - 1)) * (nB - 1)));
      const hu = img.rescale(at(img, a, b, c));
      const v = Math.max(0, Math.min(255, Math.round(((hu - lo) / (hi - lo)) * 255)));
      const oi = ox + OUT_XY * (oy + OUT_XY * oz);
      ct[oi] = v;
      sg[oi] = at(seg, a, b, c) | 0;
    }
  }
}

// ---------------------------------------------------------------------------
// 2) Build smoothed masks at MC resolution and extract isosurfaces
const maxDim = Math.max(img.nx, img.ny, img.nz);
const md = [img.nx, img.ny, img.nz].map((d) => Math.max(24, Math.round((d * MASK_MAX) / maxDim)));
console.log(`mask grid ${md.join("x")}`);

function buildMask(labelTest) {
  const [mx, my, mz] = md;
  const m = new Float32Array(mx * my * mz);
  for (let z = 0; z < mz; z++) {
    const sz = Math.min(seg.nz - 1, Math.round((z / (mz - 1)) * (seg.nz - 1)));
    for (let y = 0; y < my; y++) {
      const sy = Math.min(seg.ny - 1, Math.round((y / (my - 1)) * (seg.ny - 1)));
      for (let x = 0; x < mx; x++) {
        const sx = Math.min(seg.nx - 1, Math.round((x / (mx - 1)) * (seg.nx - 1)));
        if (labelTest(at(seg, sx, sy, sz))) m[x + mx * (y + my * z)] = 1;
      }
    }
  }
  return m;
}

// simple separable 3-tap box blur, repeated, for a smoother surface
function smooth(m, passes) {
  const [mx, my, mz] = md;
  let a = m;
  for (let p = 0; p < passes; p++) {
    const b = new Float32Array(a.length);
    for (let z = 0; z < mz; z++)
      for (let y = 0; y < my; y++)
        for (let x = 0; x < mx; x++) {
          let s = 0, n = 0;
          for (let dz = -1; dz <= 1; dz++)
            for (let dy = -1; dy <= 1; dy++)
              for (let dx = -1; dx <= 1; dx++) {
                const xx = x + dx, yy = y + dy, zz = z + dz;
                if (xx < 0 || yy < 0 || zz < 0 || xx >= mx || yy >= my || zz >= mz) continue;
                s += a[xx + mx * (yy + my * zz)];
                n++;
              }
          b[x + mx * (y + my * z)] = s / n;
        }
    a = b;
  }
  return a;
}

// world position of a mask-grid sample along IMAGE axis (a=0,b=1,c=2)
function gridToWorld(v, imgAxis) {
  const dim = md[imgAxis];
  return ((v / (dim - 1)) - 0.5) * 2 * extImg[imgAxis];
}

function meshFromMask(mask, iso = 0.5) {
  const [mx, my, mz] = md;
  const result = isosurface.marchingCubes(
    [mx, my, mz],
    (x, y, z) => {
      if (x < 0 || y < 0 || z < 0 || x >= mx || y >= my || z >= mz) return -iso;
      return mask[x + mx * (y + my * z)] - iso;
    }
  );
  // MC grid dims are [a,b,c]; remap to output (X<-b, Y<-c, Z<-a)
  const positions = new Array(result.positions.length * 3);
  for (let i = 0; i < result.positions.length; i++) {
    const p = result.positions[i]; // [pa, pb, pc]
    positions[i * 3] = +gridToWorld(p[1], 1).toFixed(4); // X <- b
    positions[i * 3 + 1] = +gridToWorld(p[2], 2).toFixed(4); // Y <- c
    positions[i * 3 + 2] = +gridToWorld(p[0], 0).toFixed(4); // Z <- a
  }
  const indices = new Array(result.cells.length * 3);
  for (let i = 0; i < result.cells.length; i++) {
    indices[i * 3] = result.cells[i][0];
    indices[i * 3 + 1] = result.cells[i][1];
    indices[i * 3 + 2] = result.cells[i][2];
  }
  return { positions, indices, vertexCount: result.positions.length, triCount: result.cells.length };
}

console.log("extracting tumour isosurface (label 2)…");
const tumorMesh = meshFromMask(smooth(buildMask((l) => l === 2), 2));
console.log(`  tumour: ${tumorMesh.vertexCount} verts / ${tumorMesh.triCount} tris`);

console.log("extracting kidney isosurface (label 1+2)…");
const kidneyMesh = meshFromMask(smooth(buildMask((l) => l >= 1), 2));
console.log(`  kidney: ${kidneyMesh.vertexCount} verts / ${kidneyMesh.triCount} tris`);

// ---------------------------------------------------------------------------
// 3) Metrics from real voxels
const voxVolMm3 = img.spacing[0] * img.spacing[1] * img.spacing[2];
let tumorVox = 0, kidneyVox = 0;
const bb = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
for (let z = 0; z < seg.nz; z++)
  for (let y = 0; y < seg.ny; y++)
    for (let x = 0; x < seg.nx; x++) {
      const l = at(seg, x, y, z);
      if (l >= 1) kidneyVox++;
      if (l === 2) {
        tumorVox++;
        bb.min[0] = Math.min(bb.min[0], x); bb.max[0] = Math.max(bb.max[0], x);
        bb.min[1] = Math.min(bb.min[1], y); bb.max[1] = Math.max(bb.max[1], y);
        bb.min[2] = Math.min(bb.min[2], z); bb.max[2] = Math.max(bb.max[2], z);
      }
    }
const tumorVolCm3 = (tumorVox * voxVolMm3) / 1000;
const kidneyVolCm3 = (kidneyVox * voxVolMm3) / 1000;
const bbMm = [
  (bb.max[0] - bb.min[0]) * img.spacing[0],
  (bb.max[1] - bb.min[1]) * img.spacing[1],
  (bb.max[2] - bb.min[2]) * img.spacing[2],
];
const maxDiameterMm = Math.max(...bbMm);
const meanDiameterMm = bbMm.reduce((a, b) => a + b, 0) / 3;

// ---------------------------------------------------------------------------
// 4) Write assets
fs.mkdirSync(OUT, { recursive: true });
fs.writeFileSync(path.join(OUT, "ct.bin.gz"), zlib.gzipSync(Buffer.from(ct.buffer)));
fs.writeFileSync(path.join(OUT, "seg.bin.gz"), zlib.gzipSync(Buffer.from(sg.buffer)));
fs.writeFileSync(path.join(OUT, "tumor.json"), JSON.stringify(tumorMesh));
fs.writeFileSync(path.join(OUT, "kidney.json"), JSON.stringify(kidneyMesh));

const manifest = {
  id: "kits_case00000",
  title: "KiTS19 · case_00000",
  source: "KiTS19 (Heller et al.) — CT imaging via HuggingFace, segmentation via github.com/neheller/kits19",
  modality: "CT · contrast",
  dims: [OUT_XY, OUT_XY, OUT_Z], // output X,Y,Z (Z = axial slice axis)
  fullDims: [nB, nC, nA], // native voxels in output order
  spacingMm: [img.spacing[1], img.spacing[2], img.spacing[0]], // output order
  worldExtent, // half-width per output axis in normalized space
  storageWindowHU: { lo: WIN_LEVEL - WIN_WIDTH / 2, hi: WIN_LEVEL + WIN_WIDTH / 2 },
  defaultWL: { window: 0.85, level: 0.5 },
  hasSegmentation: true,
  labels: { 1: "kidney", 2: "tumour" },
  timepoints: [
    {
      id: "case_00000",
      label: "case_00000",
      ct: "ct.bin.gz",
      seg: "seg.bin.gz",
      tumorMesh: "tumor.json",
      organMesh: "kidney.json",
    },
  ],
  meshes: null,
  metrics: "metrics.json",
};
fs.writeFileSync(path.join(OUT, "manifest.json"), JSON.stringify(manifest, null, 2));

const metrics = {
  tumorVolumeCm3: +tumorVolCm3.toFixed(1),
  organVolumeCm3: +kidneyVolCm3.toFixed(1),
  organLabel: "kidney",
  maxDiameterMm: +maxDiameterMm.toFixed(1),
  meanDiameterMm: +meanDiameterMm.toFixed(1),
  tumorVoxels: tumorVox,
  bboxMm: bbMm.map((v) => +v.toFixed(1)),
};
fs.writeFileSync(path.join(OUT, "metrics.json"), JSON.stringify(metrics, null, 2));

const sizes = (f) => (fs.statSync(path.join(OUT, f)).size / 1024).toFixed(0) + " KB";
console.log("\nwrote:");
for (const f of ["ct.bin.gz", "seg.bin.gz", "tumor.json", "kidney.json", "manifest.json", "metrics.json"])
  console.log(`  ${f.padEnd(14)} ${sizes(f)}`);
console.log("\nmetrics:", JSON.stringify(metrics));
