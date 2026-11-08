import { type ReactNode, Suspense } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { MainLayout } from "./components/layout/MainLayout";
import { Home } from "./pages/Home";
import {
  AboutStep,
  AppConsole,
  AppKeys,
  AppReview,
  AreaStep,
  BuilderLayout,
  EndUser,
  NewEntry,
  PastProjects,
  ReviewStep,
  SourcesStep,
} from "./routes";

/** Suspense per route element, so the header and builder chrome stay put while a page chunk loads. */
function Page({ children }: { children: ReactNode }) {
  return <Suspense fallback={null}>{children}</Suspense>;
}

function EndUserRoute() {
  // EndUser.tsx predates the router and takes its appId as a prop.
  const { appId } = useParams<{ appId: string }>();
  return (
    <Page>
      <EndUser appId={appId ?? ""} />
    </Page>
  );
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
        <Route path="/new" element={<Page><BuilderLayout /></Page>}>
          <Route index element={<Page><NewEntry /></Page>} />
          <Route path="about" element={<Page><AboutStep /></Page>} />
          <Route path="area" element={<Page><AreaStep /></Page>} />
          <Route path="sources" element={<Page><SourcesStep /></Page>} />
          <Route path="review" element={<Page><ReviewStep /></Page>} />
          <Route path="*" element={<Navigate to="/new" replace />} />
        </Route>

        <Route path="/projects" element={<Page><PastProjects /></Page>} />
        <Route path="/apps/:appId/review" element={<Page><AppReview /></Page>} />
        <Route path="/apps/:appId/keys" element={<Page><AppKeys /></Page>} />
        <Route path="/apps/:appId" element={<Page><AppConsole /></Page>} />

        {/* Old routes. */}
        <Route path="/sources" element={<Navigate to="/new" replace />} />
        <Route path="/architecture" element={<Navigate to="/new" replace />} />
        <Route path="/architecture/:appId" element={<LegacyArchitectureRedirect />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
