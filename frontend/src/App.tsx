import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { MainLayout } from "./components/layout/MainLayout";
import { Architecture } from "./pages/Architecture";
import { EndUser } from "./pages/EndUser";
import { Home } from "./pages/Home";
import { PastProjects } from "./pages/PastProjects";
import { Sources } from "./pages/Sources";

function EndUserRoute() {
  // EndUser.tsx predates the router and takes its appId as a prop.
  const { appId } = useParams<{ appId: string }>();
  return <EndUser appId={appId ?? ""} />;
}

export function App() {
  return (
    <Routes>
      {/* No MainLayout: EndUser is public-only, phone-first, and keeps its own chrome. */}
      <Route path="/apps/:appId/go" element={<EndUserRoute />} />

      <Route element={<MainLayout />}>
        <Route path="/" element={<Home />} />
        <Route path="/sources" element={<Sources />} />
        <Route path="/architecture" element={<Architecture />} />
        <Route path="/architecture/:appId" element={<Architecture />} />
        <Route path="/projects" element={<PastProjects />} />
        <Route path="/apps/:appId" element={<LegacyAppRedirect />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

function LegacyAppRedirect() {
  const { appId } = useParams<{ appId: string }>();
  return <Navigate to={`/architecture/${encodeURIComponent(appId ?? "")}`} replace />;
}
