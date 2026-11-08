import { ApiError, archApi } from "../../api/client";

/**
 * Is the app still proposed (pending_confirmation)? Used when GET /apps/{app} answers 401 without a
 * key: newer servers return a proposed spec keyless, older ones ask for a key either way. The public
 * compile-status route answers 200 while the app is not active and 404 once it is.
 * Returns true / false, or null when the server can't tell (route missing or unreachable).
 */
export async function isProposed(appId: string): Promise<boolean | null> {
  try {
    await archApi.compileStatus(appId);
    return true;
  } catch (e) {
    // FastAPI's own "Not Found" means the route is missing; the route's own 404 means "active or unknown"
    if (e instanceof ApiError && e.status === 404) return e.message === "Not Found" ? null : false;
    return null;
  }
}
