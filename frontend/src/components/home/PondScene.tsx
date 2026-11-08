// Home pond <Canvas> root (lazy-loaded by pages/Home.tsx). Owns the shared conventions in pond.ts:
// orthographic top-down camera (viewport height = 100 world units), the scene clock, pointer
// tracking + ripple ring buffer, reducedMotion/mobile flags, dpr cap (1 on low-power devices), tab-hidden and offscreen pause,
// WebGL gate.
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Component, type ReactNode, type RefObject, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { OrthographicCamera } from "three";
import { usePrefersReducedMotion } from "../../hooks/usePrefersReducedMotion";
import { useWebGLSupport } from "../../hooks/useWebGLSupport";
import FloatingLog from "./FloatingLog";
import KoiPair from "./KoiPair";
import LilyPads from "./LilyPads";
import PondBed from "./PondBed";
import Rocks from "./Rocks";
import SeaweedBed from "./SeaweedBed";
import StaticPondFallback from "./StaticPondFallback";
import WaterSurface from "./WaterSurface";
import { RIPPLE_COUNT, pond } from "./pond";

/** Top-down orthographic camera whose frustum is always 100 units tall, centred on the origin. */
function CameraRig() {
  const set = useThree((s) => s.set);
  const size = useThree((s) => s.size);
  const camera = useMemo(() => {
    const c = new OrthographicCamera(-50, 50, 50, -50, 0.1, 100);
    c.position.set(0, 0, 50);
    c.lookAt(0, 0, 0);
    // Tell R3F not to overwrite our frustum on resize.
    return Object.assign(c, { manual: true });
  }, []);

  useLayoutEffect(() => {
    const aspect = size.width / Math.max(1, size.height);
    camera.left = -50 * aspect;
    camera.right = 50 * aspect;
    camera.top = 50;
    camera.bottom = -50;
    camera.updateProjectionMatrix();
    pond.aspect = aspect;
    set({ camera });
  }, [camera, set, size.width, size.height]);

  return null;
}

/** Advances pond.time once per frame (registered before the children, so it runs first). */
function Clock() {
  useFrame((_, delta) => {
    // Clamp so a long pause (tab switch) does not jump every animation forward.
    const dt = Math.min(delta, 0.1);
    pond.time += dt * (pond.reducedMotion ? 0.2 : 1);
  });
  return null;
}

class CanvasBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? <StaticPondFallback /> : this.props.children;
  }
}

function useDocumentHidden(): boolean {
  const [hidden, setHidden] = useState(() => typeof document !== "undefined" && document.hidden);
  useEffect(() => {
    const on = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return hidden;
}

/** False while the wrapper is scrolled fully out of view, so the render loop can stop. */
function useOnScreen(ref: RefObject<HTMLElement>): boolean {
  const [onScreen, setOnScreen] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((entries) => {
      const e = entries[entries.length - 1];
      if (e) setOnScreen(e.isIntersecting);
    });
    io.observe(el);
    return () => io.disconnect();
  }, [ref]);
  return onScreen;
}

/**
 * Low-power hint (few cores or little memory): render at dpr 1 instead of up to 1.75. Normal
 * machines keep the full cap, so the pond looks the same there.
 */
function isLowPowerDevice(): boolean {
  if (typeof navigator === "undefined") return false;
  const cores = navigator.hardwareConcurrency;
  const memory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory;
  return (typeof cores === "number" && cores > 0 && cores <= 4) || (typeof memory === "number" && memory <= 4);
}

function useIsMobile(): boolean {
  const query = "(max-width: 767px)";
  const [mobile, setMobile] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMobile(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return mobile;
}

export default function PondScene() {
  const webgl = useWebGLSupport();
  const reducedMotion = usePrefersReducedMotion();
  const mobile = useIsMobile();
  const hidden = useDocumentHidden();
  const wrapRef = useRef<HTMLDivElement>(null);
  const onScreen = useOnScreen(wrapRef);
  const [lowPower] = useState(isLowPowerDevice);

  pond.reducedMotion = reducedMotion;
  pond.mobile = mobile;

  // Pointer tracking on window, mapped into the wrapper's world coords, so the canvas never has to
  // receive events and never blocks the overlay's buttons.
  useEffect(() => {
    let lastX = Infinity;
    let lastY = Infinity;
    let lastT = -Infinity;
    const toWorld = (e: PointerEvent) => {
      const el = wrapRef.current;
      if (!el) return null;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return null;
      const aspect = r.width / r.height;
      return [((e.clientX - r.left) / r.width - 0.5) * 100 * aspect, (0.5 - (e.clientY - r.top) / r.height) * 100] as const;
    };
    const emit = (x: number, y: number) => {
      const slot = pond.ripples[pond.rippleHead % RIPPLE_COUNT];
      if (slot) slot.set(x, y, pond.time);
      pond.rippleHead = (pond.rippleHead + 1) % RIPPLE_COUNT;
      lastX = x;
      lastY = y;
      lastT = pond.time;
    };
    const onMove = (e: PointerEvent) => {
      const w = toWorld(e);
      if (!w) return;
      pond.pointer.set(w[0], w[1]);
      pond.pointerActive = true;
      if (pond.reducedMotion) return;
      const moved = Math.hypot(w[0] - lastX, w[1] - lastY);
      if (moved > 5 && pond.time - lastT > 0.12) emit(w[0], w[1]);
    };
    const onDown = (e: PointerEvent) => {
      const w = toWorld(e);
      if (w && !pond.reducedMotion) emit(w[0], w[1]);
    };
    const onLeave = () => {
      pond.pointerActive = false;
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerdown", onDown, { passive: true });
    document.documentElement.addEventListener("pointerleave", onLeave);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerdown", onDown);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      pond.pointerActive = false;
    };
  }, []);

  if (!webgl) return <StaticPondFallback />;

  return (
    <div ref={wrapRef} className="absolute inset-0" aria-hidden="true">
      {/* The fallback sits underneath so there is never a blank frame while the GL context spins up. */}
      <StaticPondFallback />
      <CanvasBoundary>
        <Canvas
          className="!absolute inset-0"
          style={{ pointerEvents: "none" }}
          dpr={lowPower ? 1 : [1, 1.75]}
          flat
          frameloop={hidden || !onScreen ? "never" : "always"}
          gl={{ antialias: true, alpha: false, powerPreference: "high-performance" }}
        >
          <CameraRig />
          <Clock />
          <ambientLight intensity={0.9} />
          <hemisphereLight args={["#EAF6F4", "#8DB9B7", 0.55]} />
          <directionalLight position={[-40, 50, 60]} intensity={1.1} color="#FFF6E6" />
          {/* Each child places itself at its own pond.ts Z layer; listed here in draw order. */}
          <PondBed /* Z.BED */ />
          <Rocks /* Z.ROCKS */ />
          <SeaweedBed /* Z.SEAWEED */ />
          <KoiPair /* Z.KOI */ />
          <WaterSurface /* Z.WATER (+ ripple rings) */ />
          <LilyPads /* Z.PADS */ />
          <FloatingLog /* Z.LOG */ />
        </Canvas>
      </CanvasBoundary>
    </div>
  );
}
