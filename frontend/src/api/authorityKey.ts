import type { AppSpec } from "./types";

// Per-viewer storage of an app's authority key. Session-only by default; "remember" keeps it in
// localStorage on this device. Storage can be unavailable (private mode), so every access is guarded.

const name = (appId: string) => `authority-key:${appId}`;

function read(store: () => Storage, appId: string): string {
  try {
    return store().getItem(name(appId)) ?? "";
  } catch {
    return "";
  }
}

function write(store: () => Storage, appId: string, key: string | null): void {
  try {
    if (key) store().setItem(name(appId), key);
    else store().removeItem(name(appId));
  } catch {
    // unavailable: the key lives only in memory for this page
  }
}

export function loadKey(appId: string): { key: string; remembered: boolean } {
  const remembered = read(() => localStorage, appId);
  return remembered ? { key: remembered, remembered: true } : { key: read(() => sessionStorage, appId), remembered: false };
}

export function saveKey(appId: string, key: string, remember: boolean): void {
  write(() => sessionStorage, appId, remember ? null : key);
  write(() => localStorage, appId, remember ? key : null);
}

export function forgetKey(appId: string): void {
  write(() => sessionStorage, appId, null);
  write(() => localStorage, appId, null);
}

// --- Past Projects: a client-side-only record of apps built in this browser. There is no backend
// "list all apps" endpoint, so this is the entire mechanism -- not a cache of something fetchable. ---

export interface ProjectRecord {
  appId: string;
  intent: string;
  status: "proposed" | "active";
  updatedAt: string; // ISO
}

const PROJECTS_KEY = "past-projects";

function readProjects(): ProjectRecord[] {
  try {
    const raw = localStorage.getItem(PROJECTS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as ProjectRecord[]) : [];
  } catch {
    return [];
  }
}

function writeProjects(records: ProjectRecord[]): void {
  try {
    localStorage.setItem(PROJECTS_KEY, JSON.stringify(records));
  } catch {
    // storage unavailable (private mode): the record just doesn't persist
  }
}

export function recordProject(record: ProjectRecord): void {
  const rest = readProjects().filter((p) => p.appId !== record.appId);
  writeProjects([record, ...rest]);
}

export function listProjects(): ProjectRecord[] {
  return readProjects().sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

export function removeProject(appId: string): void {
  writeProjects(readProjects().filter((p) => p.appId !== appId));
}

// --- Architecture page additions (fe-arch-shell): pending-spec snapshots ---
// GET /v1/apps/{app} needs the authority key, and a pending app has none yet. So the Architecture page
// keeps a local copy of a *pending* spec (the dev's own, unconfirmed config; no events, keys or trace)
// to reopen it from Past Projects. Active apps are never snapshotted: they load from the server with
// the key (rule 8), and activation deletes the snapshot.
const snapName = (appId: string) => `pending-spec:${appId}`;

export function saveSpecSnapshot(spec: AppSpec): void {
  try {
    if (spec.status === "active") localStorage.removeItem(snapName(spec.app_id));
    else localStorage.setItem(snapName(spec.app_id), JSON.stringify(spec));
  } catch {
    // storage unavailable
  }
}

export function loadSpecSnapshot(appId: string): AppSpec | null {
  try {
    const raw = localStorage.getItem(snapName(appId));
    if (!raw) return null;
    const spec = JSON.parse(raw) as AppSpec;
    return spec && spec.app_id === appId && spec.status !== "active" ? spec : null;
  } catch {
    return null;
  }
}

export function dropSpecSnapshot(appId: string): void {
  try {
    localStorage.removeItem(snapName(appId));
  } catch {
    // storage unavailable
  }
}
