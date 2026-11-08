// Compatibility shim: App.tsx (owned by another agent) still routes /architecture[/:appId] here.
// Delete this file once App.tsx routes /apps/:appId (AppConsole), /apps/:appId/review (AppReview)
// and /apps/:appId/keys (AppKeys).
import { useParams } from "react-router-dom";
import AppConsole from "./AppConsole";
import AppReview from "./AppReview";

export function Architecture() {
  const { appId } = useParams<{ appId: string }>();
  return appId ? <AppConsole /> : <AppReview />;
}
