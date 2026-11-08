// One display-name rule for every page: the same as the server's PublicAppInfo.title.
// Hyphens and underscores become spaces, then each word is title-cased ("flood-watch_v2" -> "Flood Watch V2").

/** The app's display name, derived from its id. Empty for an empty id. */
export function appDisplayName(appId: string): string {
  return appId
    .split(/[-_]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
