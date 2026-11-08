import { useEffect, useState } from "react";
import { AppPage } from "./pages/AppPage";
import { Builder } from "./pages/Builder";
import { EndUser } from "./pages/EndUser";

// Minimal path routing (no router dependency):
//   /                  -> Builder
//   /apps/:appId       -> AppPage (dev console, authority view)
//   /apps/:appId/go    -> EndUser (public instructions only)
function route(path: string) {
  const m = path.match(/^\/apps\/([^/]+)(\/go)?\/?$/);
  if (!m?.[1]) return <Builder />;
  const appId = decodeURIComponent(m[1]);
  return m[2] ? <EndUser appId={appId} /> : <AppPage appId={appId} />;
}

export function App() {
  const [health, setHealth] = useState("checking");

  useEffect(() => {
    fetch("/v1/health")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((b: { status: string }) => setHealth(b.status))
      .catch(() => setHealth("backend unreachable"));
  }, []);

  return (
    <>
      <header style={{ padding: "8px 16px", borderBottom: "1px solid #ddd", fontFamily: "system-ui" }}>
        Geo heat-map platform · API: {health}
      </header>
      <main style={{ padding: 16, fontFamily: "system-ui" }}>{route(window.location.pathname)}</main>
    </>
  );
}
