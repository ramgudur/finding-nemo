import { useEffect, useState } from "react";

function probe(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return !!(canvas.getContext("webgl2") || canvas.getContext("webgl") || canvas.getContext("experimental-webgl"));
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
