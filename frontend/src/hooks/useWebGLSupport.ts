import { useEffect, useState } from "react";

function probe(): boolean {
  try {
    const canvas = document.createElement("canvas");
    const gl = (canvas.getContext("webgl2") || canvas.getContext("webgl") || canvas.getContext("experimental-webgl")) as
      | WebGLRenderingContext
      | null;
    // Release the probe context right away; browsers cap live contexts and this one is never drawn to.
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
    return !!gl;
  } catch {
    return false;
  }
}

/** Capability gate for the pond: false means render StaticPondFallback instead of <Canvas>. */
export function useWebGLSupport(): boolean {
  const [supported, setSupported] = useState(true);
  useEffect(() => setSupported(probe()), []);
  return supported;
}
