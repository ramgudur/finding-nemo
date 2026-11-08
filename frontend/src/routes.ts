// Lazy route modules. Home stays in the entry chunk (it is the first paint); every other page,
// and Leaflet with it, loads on first visit. Home prefetches the two pages its buttons open.
import { lazy } from "react";

export const loadBuilder = () => import("./pages/builder");
export const loadPastProjects = () => import("./pages/PastProjects");

export const BuilderLayout = lazy(() => loadBuilder().then((m) => ({ default: m.BuilderLayout })));
export const NewEntry = lazy(() => loadBuilder().then((m) => ({ default: m.NewEntry })));
export const AboutStep = lazy(() => loadBuilder().then((m) => ({ default: m.AboutStep })));
export const AreaStep = lazy(() => loadBuilder().then((m) => ({ default: m.AreaStep })));
export const SourcesStep = lazy(() => loadBuilder().then((m) => ({ default: m.SourcesStep })));
export const ReviewStep = lazy(() => loadBuilder().then((m) => ({ default: m.ReviewStep })));
export const PastProjects = lazy(() => loadPastProjects().then((m) => ({ default: m.PastProjects })));
export const AppConsole = lazy(() => import("./pages/AppConsole"));
export const AppKeys = lazy(() => import("./pages/AppKeys"));
export const AppReview = lazy(() => import("./pages/AppReview"));
export const EndUser = lazy(() => import("./pages/EndUser").then((m) => ({ default: m.EndUser })));

/** Warm the chunks behind Home's two buttons once the browser is idle (after the pond has started). */
export function prefetchHomeTargets(): () => void {
  const run = () => {
    void loadBuilder().catch(() => {});
    void loadPastProjects().catch(() => {});
  };
  const w = window as Window & {
    requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
    cancelIdleCallback?: (id: number) => void;
  };
  let idle: number | undefined;
  const timer = window.setTimeout(() => {
    if (w.requestIdleCallback) idle = w.requestIdleCallback(run, { timeout: 4000 });
    else run();
  }, 2500);
  return () => {
    window.clearTimeout(timer);
    if (idle !== undefined) w.cancelIdleCallback?.(idle);
  };
}
