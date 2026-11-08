// Who moves the data, at a glance: push is the dev's app sending to us (an arrow in), pull is the platform
// fetching it on a schedule (a refresh loop). Used by the suggestion card, the source rows and Review.
import { ArrowDownToLine, RefreshCw } from "lucide-react";

export function KindBadge({ kind, small }: { kind: "push" | "pull"; small?: boolean }) {
  const Icon = kind === "push" ? ArrowDownToLine : RefreshCw;
  return (
    <span
      title={kind === "push" ? "Your app sends this to the platform" : "The platform fetches this on a schedule"}
      className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full font-semibold ${
        small ? "px-1.5 py-px text-[11px]" : "px-2 py-0.5 text-xs"
      } ${kind === "push" ? "bg-primary/15 text-primary" : "bg-mutedTeal/20 text-[#3C6668]"}`}
    >
      <Icon size={small ? 10 : 11} strokeWidth={2.4} aria-hidden />
      {kind === "push" ? "from your app" : "we fetch"}
    </span>
  );
}
