// The public end-user link (/apps/:id/go) with copy and a client-side QR code, shared by the console
// header and the keys page. VITE_PUBLIC_BASE (a public host or tunnel) wins over this page's origin.
import { QrCode as QrIcon, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { CopyButton } from "./AppShell";
import { encodeQr, qrPath } from "./qr";

const PUBLIC_BASE_ENV = ((import.meta.env.VITE_PUBLIC_BASE as string | undefined) ?? "").trim().replace(/\/+$/, "");

export function publicBase(): string {
  return PUBLIC_BASE_ENV || window.location.origin;
}

export function publicGoLink(appId: string): string {
  return `${publicBase()}/apps/${encodeURIComponent(appId)}/go`;
}

/** True when the link only works on this computer (no public host configured). */
export function isLocalBase(base = publicBase()): boolean {
  return /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/i.test(base);
}

/** A QR code as one SVG path; dark slate on cream, with the standard quiet zone. Null when too long. */
export function QrSvg({ value, size = 168, className = "" }: { value: string; size?: number; className?: string }) {
  const qr = useMemo(() => encodeQr(value), [value]);
  if (!qr) return null;
  const n = qr.size + 8;
  return (
    <svg
      role="img"
      aria-label={`QR code for ${value}`}
      viewBox={`0 0 ${n} ${n}`}
      width={size}
      height={size}
      shapeRendering="crispEdges"
      className={`rounded-xl ${className}`}
    >
      <rect width={n} height={n} className="fill-sheet" />
      <path d={qrPath(qr)} className="fill-deep2" />
    </svg>
  );
}

/** Keys page: the link, copy, and an inline "Show as QR" disclosure. */
export function PublicLinkRow({ link }: { link: string }) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const fits = useMemo(() => encodeQr(link) !== null, [link]);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <a
          href={link}
          target="_blank"
          rel="noreferrer"
          className="min-w-0 flex-1 basis-[240px] break-all rounded-[10px] bg-sheet px-3 py-2 font-mono text-[13px] text-slate shadow-[inset_0_0_0_1px_rgb(var(--c-slate)/0.18)] underline decoration-deep/40 underline-offset-2 transition-colors hover:decoration-slate"
        >
          {link}
        </a>
        <CopyButton value={link} />
        {fits && (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={panel}
            onClick={() => setOpen((o) => !o)}
            className="tap wp-press inline-flex shrink-0 items-center gap-1.5 rounded-full bg-sheet px-3 py-1 text-[12.5px] font-bold text-slate shadow-[inset_0_0_0_1px_rgb(var(--c-slate)/0.24)] hover:shadow-[inset_0_0_0_1px_rgb(var(--c-ink-soft))]"
          >
            <QrIcon aria-hidden className="h-3.5 w-3.5" />
            {open ? "Hide QR" : "Show as QR"}
          </button>
        )}
      </div>
      {open && (
        <div id={panel} className="flex flex-wrap items-center gap-4">
          <QrSvg value={link} size={176} className="shadow-[var(--shadow-1)]" />
          <p className="max-w-[36ch] text-[13px] text-ink">Point a phone camera at it to open the end-user page. Print it or put it on a slide for people nearby.</p>
        </div>
      )}
    </div>
  );
}

/** Console header: copy plus a small QR popover anchored under the button. */
export function PublicLinkButtons({ link }: { link: string }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const panel = useId();
  const fits = useMemo(() => encodeQr(link) !== null, [link]);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div ref={wrap} className="relative flex items-center gap-2">
      <CopyButton value={link} label="Copy public link" />
      {fits && (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panel}
          aria-label="Show the public link as a QR code"
          onClick={() => setOpen((o) => !o)}
          className="tap wp-press inline-flex shrink-0 items-center gap-1.5 rounded-full bg-sheet px-3 py-1 text-[12.5px] font-bold text-slate shadow-[inset_0_0_0_1px_rgb(var(--c-slate)/0.24)] transition-[background-color,box-shadow] duration-150 hover:bg-white hover:shadow-sm"
        >
          <QrIcon aria-hidden className="h-3.5 w-3.5" /> QR
        </button>
      )}
      {open && (
        <div
          id={panel}
          role="dialog"
          aria-label="Public link QR code"
          className="absolute right-0 top-full z-[1100] mt-2 w-[236px] space-y-2 rounded-2xl bg-sheet p-3 shadow-[var(--shadow-2)]"
        >
          <div className="flex items-center justify-between">
            <p className="text-[13px] font-bold text-slate">End-user page</p>
            <button type="button" onClick={() => setOpen(false)} aria-label="Close" className="hit relative rounded p-0.5 text-ink hover:text-slate">
              <X aria-hidden className="h-4 w-4" />
            </button>
          </div>
          <QrSvg value={link} size={210} />
          <p className="break-all font-mono text-[11px] leading-snug text-ink">{link}</p>
          {isLocalBase() && <p className="text-[12px] text-danger">A localhost link only opens on this computer. Set VITE_PUBLIC_BASE to a public host or tunnel.</p>}
        </div>
      )}
    </div>
  );
}
