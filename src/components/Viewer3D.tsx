import { useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { useStore } from "../store";
import { makeRealSliceTexture, imageToTexture } from "../lib/sliceTexture";
import { sliceWorldZ, type MeshData } from "../lib/dataset";
import { renderReformat, realSampler, type PlaneBasis, type PlaneKind } from "../lib/mpr";

// Authored in normalized coordinates, then this group rotates the cranio-caudal
// (z) axis to vertical so the body "stands up" in the scene.
const TO_SCENE: [number, number, number] = [-Math.PI / 2, 0, 0];

function meshToGeometry(m: MeshData): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(m.positions, 3));
  g.setIndex(new THREE.BufferAttribute(m.indices, 1));
  g.computeVertexNormals();
  g.computeBoundingSphere();
  return g;
}

// current timepoint's voxel data
function useTP() {
  const real = useStore((s) => s.real);
  const timepoint = useStore((s) => s.timepoint);
  if (!real) return null;
  const tp = real.timepoints[timepoint];
  return { ct: tp.ct, seg: tp.seg, manifest: real.manifest, real };
}

function Scene() {
  const real = useStore((s) => s.real);
  const timepoint = useStore((s) => s.timepoint);
  const { showBody, showTumor } = useStore();
  const tp = real?.timepoints[timepoint];
  const tumorGeo = useMemo(() => (tp?.tumorMesh ? meshToGeometry(tp.tumorMesh) : null), [tp]);
  const organGeo = useMemo(() => (tp?.organMesh ? meshToGeometry(tp.organMesh) : null), [tp]);
  if (!real) return null;
  return (
    <group>
      {showBody && organGeo && (
        <>
          <mesh geometry={organGeo}>
            <meshStandardMaterial color={"#3a8294"} transparent opacity={0.18} roughness={0.7} side={THREE.DoubleSide} depthWrite={false} />
          </mesh>
          <mesh geometry={organGeo}>
            <meshBasicMaterial color={"#4fa6b8"} wireframe transparent opacity={0.1} />
          </mesh>
        </>
      )}
      {showTumor && tumorGeo && (
        <>
          <mesh geometry={tumorGeo}>
            <meshStandardMaterial color={"#ff9d3c"} emissive={"#7a3200"} emissiveIntensity={0.4} roughness={0.4} metalness={0.05} />
          </mesh>
          <mesh geometry={tumorGeo} scale={1.05}>
            <meshBasicMaterial color={"#ffd9a0"} transparent opacity={0.08} side={THREE.BackSide} depthWrite={false} />
          </mesh>
        </>
      )}
      <CutPlane />
      <LayerStack />
      <MPRBox />
    </group>
  );
}

function CutPlane() {
  const tp = useTP();
  const { slice, window, level, showCutPlane, showTumor } = useStore();
  const tex = useMemo(
    () => (tp ? makeRealSliceTexture(tp.ct, tp.seg, tp.manifest, slice, { window, level, showTumor }) : null),
    [tp, slice, window, level, showTumor]
  );
  if (!tp || !showCutPlane || !tex) return null;
  const m = tp.manifest;
  return <SlicePlane z={sliceWorldZ(slice, m)} w={2 * m.worldExtent[0]} h={2 * m.worldExtent[1]} tex={tex} />;
}

function LayerStack() {
  const tp = useTP();
  const { showLayers, window, level, showTumor } = useStore();
  const layers = useMemo(() => {
    if (!tp || !showLayers) return [];
    const m = tp.manifest;
    const out: { z: number; tex: THREE.CanvasTexture }[] = [];
    const Z = m.dims[2];
    const step = Math.max(8, Math.round(Z / 22));
    for (let k = step; k < Z - step; k += step)
      out.push({ z: sliceWorldZ(k, m), tex: makeRealSliceTexture(tp.ct, tp.seg, m, k, { window, level, showTumor }) });
    return out;
  }, [tp, showLayers, window, level, showTumor]);
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
  const { window, level, showTumor, crossX, crossY, slice, sliceMax, obliqueAngle } = useStore();
  const { tex, geo, edges } = useMemo(() => {
    const cross = { x: crossX, y: crossY, z: slice / sliceMax };
    const { sampler, ext } = realSampler(tp.ct, tp.seg, tp.manifest, window, level);
    const rf = renderReformat(kind, sampler, ext, cross, { angleDeg: obliqueAngle, showTumor, base: 200, transparentAir: true });
    const geo = buildQuad(rf.basis);
    return { tex: imageToTexture(rf.img, false), geo, edges: new THREE.EdgesGeometry(geo) };
  }, [kind, tp, window, level, showTumor, crossX, crossY, slice, sliceMax, obliqueAngle]);

  return (
    <group>
      <mesh geometry={geo}>
        <meshBasicMaterial map={tex} transparent opacity={0.92} side={THREE.DoubleSide} depthWrite={false} />
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
  const edge = useMemo(() => new THREE.PlaneGeometry(w, h), [w, h]);
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
  const geo = useMemo(() => new THREE.BoxGeometry(2 * ext[0], 2 * ext[1], 2 * ext[2]), [ext]);
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
