import * as THREE from "three";
import { renderRealSlice, type DisplayMode, type LabelStyle, type Manifest } from "./dataset";

export function imageToTexture(img: ImageData, flipY = true): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = img.width;
  canvas.height = img.height;
  canvas.getContext("2d")!.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.flipY = flipY;
  tex.needsUpdate = true;
  return tex;
}

export function makeRealSliceTexture(
  ct: Uint8Array,
  seg: Uint8Array | undefined,
  manifest: Manifest,
  k: number,
  opts: { window: number; level: number; labelStyle: LabelStyle; mri?: Uint8Array; mode?: DisplayMode; fusion?: number }
): THREE.CanvasTexture {
  return imageToTexture(renderRealSlice(ct, seg, manifest, k, { ...opts, transparentAir: true }));
}
