// Page 1: the painted koi pond (WebGL, lazy-loaded so the HTML hero paints first) + the hero UI.
import { Suspense, lazy } from "react";
import HomeOverlay from "../components/home/HomeOverlay";
import StaticPondFallback from "../components/home/StaticPondFallback";

const PondScene = lazy(() => import("../components/home/PondScene"));

export function Home() {
  return (
    <main className="relative h-[100svh] min-h-[480px] w-full overflow-hidden bg-[#BBDCE3]">
      <Suspense fallback={<StaticPondFallback />}>
        <PondScene />
      </Suspense>
      <HomeOverlay />
    </main>
  );
}
