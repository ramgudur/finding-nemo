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
