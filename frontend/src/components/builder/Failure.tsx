// A suggest or compile failure in the dev's words: what went wrong and what to do next. The server's own
// validator messages are listed as-is (they name the spec field), under plain headings.
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { CompileFailure } from "../../api/types";

export interface Failure {
  title: string;
  reason: string;
  errors: string[];
  warnings: string[];
  link?: { to: string; label: string };
}

function isCompileFailure(d: unknown): d is CompileFailure {
  return !!d && typeof d === "object" && typeof (d as CompileFailure).reason === "string";
}

export function toFailure(e: unknown, what: "compile" | "suggest", appId: string): Failure {
  if (!(e instanceof ApiError)) {
    return {
      title: "Couldn't reach the platform",
      reason: "Check that the backend is running, then try again.",
      errors: [String(e)],
      warnings: [],
    };
  }
  if (e.status === 409) {
    return {
      title: "This app is already live",
      reason: "A live app is never compiled again. Open it, or go back to About and pick a new app id.",
      errors: [],
      warnings: [],
      link: { to: `/apps/${encodeURIComponent(appId)}`, label: "Open the live app" },
    };
  }
  if (e.status === 503 || e.status === 429) {
    const msg = e.message.replace(/[.\s]+$/, "");
    const wait = /try again/i.test(msg)
      ? ""
      : e.retryAfterS != null
        ? ` Try again in about ${Math.ceil(e.retryAfterS)} s.`
        : " Try again in a minute.";
    return { title: "The model is busy right now", reason: `${msg}.${wait}`, errors: [], warnings: [] };
  }
  if (e.status === 422 && isCompileFailure(e.detail)) {
    return {
      title: "The Compiler couldn't finish a valid plan",
      reason: `${e.detail.reason} Adjust the intent or sources and compile again.`,
      errors: e.detail.errors ?? [],
      warnings: e.detail.warnings ?? [],
    };
  }
  if (e.status === 422) {
    return {
      title: what === "compile" ? "The platform rejected this draft" : "That description was rejected",
      reason: e.details.length ? "Fix these and try again:" : e.message,
      errors: e.details,
      warnings: [],
    };
  }
  return {
    title: what === "compile" ? "Compile failed" : "Couldn't get suggestions",
    reason: e.message,
    errors: [],
    warnings: [],
  };
}

export function FailurePanel({ failure, onClose }: { failure: Failure; onClose?: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "nearest" });
  }, [failure]);
  return (
    <div
      ref={ref}
      role="alert"
      className="flex scroll-mb-32 flex-col gap-2 rounded-2xl bg-attnBg px-5 py-4 text-sm text-slate shadow-[var(--shadow-1)]"
    >
      <div className="flex items-start gap-3">
        <strong className="font-semibold text-danger">{failure.title}</strong>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded-md px-1 text-xs text-ink underline-offset-2 hover:underline"
          >
            Dismiss
          </button>
        )}
      </div>
      <p className="text-slate">{failure.reason}</p>
      {failure.link && (
        <Link to={failure.link.to} className="self-start font-semibold text-primary underline underline-offset-[3px]">
          {failure.link.label}
        </Link>
      )}
      {failure.errors.length > 0 && (
        <div>
          <span className="text-xs font-semibold text-danger">Problems</span>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 font-mono text-[12px] text-slate">
            {failure.errors.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      {failure.warnings.length > 0 && (
        <div>
          <span className="text-xs font-semibold text-ink">Warnings</span>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 font-mono text-[12px] text-ink">
            {failure.warnings.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
