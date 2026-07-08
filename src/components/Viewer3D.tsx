import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { useStore } from "../store";
import { makeRealSliceTexture, imageToTexture } from "../lib/sliceTexture";
import { buildLabelStyle, sliceWorldZ, type MeshData } from "../lib/dataset";
import { renderReformat, realSampler, type PlaneBasis, type PlaneKind } from "../lib/mpr";

// Authored in normalized coordinates, then this group rotates the cranio-caudal
// (z) axis to vertical so the body "stands up" in the scene.
const TO_SCENE: [number, number, number] = [-Math.PI / 2, 0, 0];

const rgbCss = (c: [number, number, number]) => `rgb(${c[0]},${c[1]},${c[2]})`;

// Three.js never auto-frees imperatively-created geometries/textures (react-three-
// fiber only disposes the ones declared as JSX), so each slice scrub or patient
// switch that rebuilds a CanvasTexture/BufferGeometry would leak GPU memory until
// the WebGL context is lost. Free each resource when React replaces it or unmounts
// the owning component — the standard effect-cleanup pattern, keyed on identity.
//
// Disposing a Three object only releases its GPU handles; the CPU-side data stays,
// so if StrictMode runs the cleanup an extra time in dev the object just re-uploads
// on the next frame. No render-phase trickery needed.
function disposeDeep(v: unknown): void {
  if (!v || typeof v !== "object") return;
  if (typeof (v as { dispose?: unknown }).dispose === "function") {
    (v as { dispose: () => void }).dispose();
    return; // a THREE object owns its internals — don't recurse past it
  }
  if (Array.isArray(v)) {
    for (const x of v) disposeDeep(x);
    return;
  }
  for (const k in v) disposeDeep((v as Record<string, unknown>)[k]);
}

function useDisposable<T>(value: T): T {
  useEffect(() => () => disposeDeep(value), [value]);
  return value;
}

function meshToGeometry(m: MeshData): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(m.positions, 3));
  g.setIndex(new THREE.BufferAttribute(m.indices, 1));
  g.computeVertexNormals();
  g.computeBoundingSphere();
  return g;
}

// current timepoint's voxel data. Memoized so the returned object keeps a stable
// identity across renders that don't change the dataset/timepoint — a fresh literal
// here would invalidate every downstream useMemo (slice textures, reformats) on any
// unrelated store change (e.g. dragging the crosshair), re-uploading GPU textures
// each time despite nothing they depend on having changed.
function useTP() {
  const real = useStore((s) => s.real);
  const timepoint = useStore((s) => s.timepoint);
  return useMemo(() => {
    if (!real) return null;
    const tp = real.timepoints[timepoint];
    return { ct: tp.ct, mri: tp.mri, seg: tp.seg, manifest: real.manifest, real };
  }, [real, timepoint]);
}

function Scene() {
  const real = useStore((s) => s.real);
  const timepoint = useStore((s) => s.timepoint);
  const labelVisible = useStore((s) => s.labelVisible);
  const tp = real?.timepoints[timepoint];
  const labelStyle = buildLabelStyle(real?.manifest, labelVisible);
  // Unify legacy single-mesh slots (2 = tumour, 1 = organ) with the per-label mesh list,
  // then style by label: the tumour (2) is a solid glowing body, everything else a
  // translucent coloured shell — so nested envelopes (body ⊃ organ ⊃ tumour) read clearly.
  const meshes = useDisposable(useMemo(() => {
    const items: { label: number; geo: THREE.BufferGeometry }[] = [];
    if (tp?.tumorMesh) items.push({ label: 2, geo: meshToGeometry(tp.tumorMesh) });
    if (tp?.organMesh) items.push({ label: 1, geo: meshToGeometry(tp.organMesh) });
    for (const mm of tp?.meshes ?? []) items.push({ label: mm.label, geo: meshToGeometry(mm.mesh) });
    return items;
  }, [tp]));
  if (!real) return null;
  return (
    <group>
      {meshes.map(({ label, geo }) => {
        const c = labelStyle[label];
        if (!c) return null;
        const col = rgbCss(c);
        if (label === 2) {
          // solid glowing tumour (no halo shell — it scaled about the world origin, not
          // the tumour centroid, so it rendered as a one-sided offset ghost)
          return (
            <mesh key={label} geometry={geo}>
              <meshStandardMaterial color={col} emissive={col} emissiveIntensity={0.22} roughness={0.4} metalness={0.05} />
            </mesh>
          );
        }
        // FrontSide + renderOrder keeps the translucent shell's look stable as the camera orbits
        return (
          <mesh key={label} geometry={geo} renderOrder={1}>
            <meshStandardMaterial color={col} transparent opacity={0.26} roughness={0.7} side={THREE.FrontSide} depthWrite={false} />
          </mesh>
        );
      })}
      <CutPlane />
      <LayerStack />
      <MPRBox />
    </group>
  );
}

function CutPlane() {
  const tp = useTP();
  const { slice, window, level, showCutPlane, displayModality, fusionAlpha } = useStore();
  const labelVisible = useStore((s) => s.labelVisible);
  const mode = tp?.mri ? displayModality : "ct";
  const labelStyle = buildLabelStyle(tp?.manifest, labelVisible);
  const tex = useDisposable(useMemo(
    () => (tp ? makeRealSliceTexture(tp.ct, tp.seg, tp.manifest, slice, { window, level, labelStyle, mri: tp.mri, mode, fusion: fusionAlpha }) : null),
    [tp, slice, window, level, labelVisible, mode, fusionAlpha]
  ));
  if (!tp || !showCutPlane || !tex) return null;
  const m = tp.manifest;
  return <SlicePlane z={sliceWorldZ(slice, m)} w={2 * m.worldExtent[0]} h={2 * m.worldExtent[1]} tex={tex} />;
}

function LayerStack() {
  const tp = useTP();
  const { showLayers, window, level, displayModality, fusionAlpha } = useStore();
  const labelVisible = useStore((s) => s.labelVisible);
  const mode = tp?.mri ? displayModality : "ct";
  const labelStyle = buildLabelStyle(tp?.manifest, labelVisible);
  const layers = useMemo(() => {
    if (!tp || !showLayers) return [];
    const m = tp.manifest;
    const out: { z: number; tex: THREE.CanvasTexture }[] = [];
    const Z = m.dims[2];
    const step = Math.max(8, Math.round(Z / 22));
    for (let k = step; k < Z - step; k += step)
      out.push({ z: sliceWorldZ(k, m), tex: makeRealSliceTexture(tp.ct, tp.seg, m, k, { window, level, labelStyle, mri: tp.mri, mode, fusion: fusionAlpha }) });
    return out;
  }, [tp, showLayers, window, level, labelVisible, mode, fusionAlpha]);
  useDisposable(layers); // frees the per-layer CanvasTextures when the stack rebuilds
  if (!tp || !showLayers) return null;
  const m = tp.manifest;
  return <LayerMeshes layers={layers} w={2 * m.worldExtent[0]} h={2 * m.worldExtent[1]} />;
}

/* ------------------------- 3D MPR orthogonal box -------------------------- */
function buildQuad(b: PlaneBasis): THREE.BufferGeometry {
  const corner = (sx: number, sy: number): [number, number, number] => [
    b.C[0] + sx * b.uExt * b.U[0] + sy * b.vExt * b.V[0],
    b.C[1] + sx * b.uExt * b.U[1] + sy * b.vExt * b.V[1],
    b.C[2] + sx * b.uExt * b.U[2] + sy * b.vExt * b.V[2],
  ];
  const A = corner(-1, -1), B = corner(1, -1), C2 = corner(1, 1), D = corner(-1, 1);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute([...A, ...B, ...C2, ...D], 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute([0, 1, 1, 1, 1, 0, 0, 0], 2));
  g.setIndex([0, 1, 2, 0, 2, 3]);
  return g;
}

function MPRQuad({ kind, color, tp }: { kind: PlaneKind; color: string; tp: NonNullable<ReturnType<typeof useTP>> }) {
  const { window, level, crossX, crossY, slice, sliceMax, obliqueAngle, displayModality, fusionAlpha } = useStore();
  const labelVisible = useStore((s) => s.labelVisible);
  const mode = tp.mri ? displayModality : "ct";
  const labelStyle = buildLabelStyle(tp.manifest, labelVisible);
  const { tex, geo, edges } = useDisposable(useMemo(() => {
    const cross = { x: crossX, y: crossY, z: slice / sliceMax };
    const { sampler, ext } = realSampler(tp.ct, tp.seg, tp.manifest, window, level, { mri: tp.mri, mode, fusion: fusionAlpha });
    const rf = renderReformat(kind, sampler, ext, cross, { angleDeg: obliqueAngle, labelStyle, base: 200, transparentAir: true });
    const geo = buildQuad(rf.basis);
    return { tex: imageToTexture(rf.img, false), geo, edges: new THREE.EdgesGeometry(geo) };
  }, [kind, tp, window, level, labelVisible, crossX, crossY, slice, sliceMax, obliqueAngle, mode, fusionAlpha]));

  return (
    <group>
      {/* The coronal & sagittal quads intersect, so they must resolve front/back
          PER PIXEL, not per object. depthWrite must stay on for that; alphaTest
          discards the transparent-air fragments (binary alpha from shade()) so
          they don't write depth and punch holes — that cutout is why depthWrite
          was originally off. Without this, whichever quad draws last always wins
          the crossing region (both centroids coincide, so the transparent sort
          is a tie), regardless of which is actually in front. */}
      <mesh geometry={geo}>
        <meshBasicMaterial map={tex} transparent opacity={0.92} alphaTest={0.5} side={THREE.DoubleSide} />
      </mesh>
      <lineSegments geometry={edges}>
        <lineBasicMaterial color={color} transparent opacity={0.55} />
      </lineSegments>
    </group>
  );
}

function MPRBox() {
  const tp = useTP();
  const showMPRPlanes = useStore((s) => s.showMPRPlanes);
  if (!tp || !showMPRPlanes) return null;
  return (
    <>
      <MPRQuad kind="coronal" color="#7fdca0" tp={tp} />
      <MPRQuad kind="sagittal" color="#7fb6e0" tp={tp} />
    </>
  );
}

/* ------------------------------- shared bits ------------------------------ */
function SlicePlane({ z, w, h, tex }: { z: number; w: number; h: number; tex: THREE.Texture }) {
  const edge = useDisposable(useMemo(() => new THREE.PlaneGeometry(w, h), [w, h]));
  return (
    <group>
      <mesh position={[0, 0, z]}>
        <planeGeometry args={[w, h]} />
        <meshBasicMaterial map={tex} transparent opacity={0.95} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      <lineSegments position={[0, 0, z]}>
        <edgesGeometry args={[edge]} />
        <lineBasicMaterial color={"#38e1d6"} transparent opacity={0.85} />
      </lineSegments>
    </group>
  );
}

function LayerMeshes({ layers, w, h }: { layers: { z: number; tex: THREE.CanvasTexture }[]; w: number; h: number }) {
  return (
    <group>
      {layers.map((l, i) => (
        <mesh key={i} position={[0, 0, l.z]}>
          <planeGeometry args={[w, h]} />
          <meshBasicMaterial map={l.tex} transparent opacity={0.32} side={THREE.DoubleSide} depthWrite={false} blending={THREE.AdditiveBlending} />
        </mesh>
      ))}
    </group>
  );
}

function Rig({ children }: { children: React.ReactNode }) {
  const ref = useRef<THREE.Group>(null);
  const autoRotate = useStore((s) => s.autoRotate);
  useFrame((_, dt) => {
    if (ref.current && autoRotate) ref.current.rotation.z += dt * 0.18;
  });
  return (
    <group rotation={TO_SCENE}>
      <group ref={ref}>{children}</group>
    </group>
  );
}

function BoundingCage({ ext }: { ext: [number, number, number] }) {
  const geo = useDisposable(useMemo(() => new THREE.BoxGeometry(2 * ext[0], 2 * ext[1], 2 * ext[2]), [ext]));
  return (
    <lineSegments>
      <edgesGeometry args={[geo]} />
      <lineBasicMaterial color={"#1c2e33"} transparent opacity={0.6} />
    </lineSegments>
  );
}

export default function Viewer3D() {
  const real = useStore((s) => s.real);
  const ext: [number, number, number] = real ? real.manifest.worldExtent : [1, 1, 0.65];

  return (
    <Canvas camera={{ position: [2.6, 1.7, 2.9], fov: 38 }} gl={{ antialias: true, alpha: true }} dpr={[1, 2]}>
      <color attach="background" args={["#070a0c"]} />
      <fog attach="fog" args={["#070a0c", 6, 12]} />
      <ambientLight intensity={0.55} />
      <directionalLight position={[4, 6, 3]} intensity={1.1} color={"#cfeff0"} />
      <directionalLight position={[-4, -2, -3]} intensity={0.4} color={"#ff9d6b"} />
      <pointLight position={[0, 0, 0]} intensity={6} distance={3} color={"#ffb454"} />

      <Rig>
        <BoundingCage ext={ext} />
        <Scene />
      </Rig>

      <OrbitControls enablePan={false} minDistance={1.6} maxDistance={7} enableDamping dampingFactor={0.08} />
    </Canvas>
  );
}
