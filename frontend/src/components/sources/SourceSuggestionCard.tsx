// Who moves the data, at a glance: push is the dev's app sending to us (an arrow in), pull is the platform
// fetching it on a schedule (a refresh loop). Used by the suggestion card, the source rows and Review.
import { ArrowDownToLine, RefreshCw } from "lucide-react";

export function KindBadge({ kind, small }: { kind: "push" | "pull"; small?: boolean }) {
  const Icon = kind === "push" ? ArrowDownToLine : RefreshCw;
  return (
    <span
      title={kind === "push" ? "Your app sends this to the platform" : "The platform fetches this on a schedule"}
      className={`wp-chip shrink-0 ${small ? "!h-5 !px-1.5 !text-[11.5px]" : ""} ${
        kind === "push" ? "bg-deep/[0.11] text-deep" : "bg-live/[0.13] text-live"
      }`}
    >
      <Icon size={small ? 10 : 11} strokeWidth={2.4} aria-hidden />
      {kind === "push" ? "from your app" : "we fetch"}
    </span>
  );
}
