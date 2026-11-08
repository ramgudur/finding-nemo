import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { MainLayout } from "./components/layout/MainLayout";
import AppConsole from "./pages/AppConsole";
import AppKeys from "./pages/AppKeys";
import AppReview from "./pages/AppReview";
import { EndUser } from "./pages/EndUser";
import { Home } from "./pages/Home";
import { PastProjects } from "./pages/PastProjects";
import { AboutStep } from "./pages/builder/AboutStep";
import { AreaStep } from "./pages/builder/AreaStep";
import { BuilderLayout } from "./pages/builder/BuilderLayout";
import { NewEntry } from "./pages/builder/NewEntry";
import { ReviewStep } from "./pages/builder/ReviewStep";
import { SourcesStep } from "./pages/builder/SourcesStep";

function EndUserRoute() {
  // EndUser.tsx predates the router and takes its appId as a prop.
  const { appId } = useParams<{ appId: string }>();
  return <EndUser appId={appId ?? ""} />;
}

/** Old /architecture/:appId links (bookmarks, Past Projects records) now live at /apps/:appId. */
function LegacyArchitectureRedirect() {
  const { appId } = useParams<{ appId: string }>();
  return <Navigate to={`/apps/${encodeURIComponent(appId ?? "")}`} replace />;
}

export function App() {
  return (
    <Routes>
      {/* No MainLayout: EndUser is public-only, phone-first, and keeps its own chrome. */}
      <Route path="/apps/:appId/go" element={<EndUserRoute />} />

      <Route element={<MainLayout />}>
        <Route path="/" element={<Home />} />

        {/* The stepped builder: 1 About · 2 Area · 3 Sources · 4 Review & compile. */}
        <Route path="/new" element={<BuilderLayout />}>
          <Route index element={<NewEntry />} />
          <Route path="about" element={<AboutStep />} />
          <Route path="area" element={<AreaStep />} />
          <Route path="sources" element={<SourcesStep />} />
          <Route path="review" element={<ReviewStep />} />
          <Route path="*" element={<Navigate to="/new" replace />} />
        </Route>

        <Route path="/projects" element={<PastProjects />} />
        <Route path="/apps/:appId/review" element={<AppReview />} />
        <Route path="/apps/:appId/keys" element={<AppKeys />} />
        <Route path="/apps/:appId" element={<AppConsole />} />

        {/* Old routes. */}
        <Route path="/sources" element={<Navigate to="/new" replace />} />
        <Route path="/architecture" element={<Navigate to="/new" replace />} />
        <Route path="/architecture/:appId" element={<LegacyArchitectureRedirect />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
