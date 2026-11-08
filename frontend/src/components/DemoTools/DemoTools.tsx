// DEMO_MODE tools for the authority console: SimControls + ErrorChart. Renders nothing when the server
// isn't in demo mode (/v1/debug/* is 404), so production consoles never show ground-truth data.
import { useEffect, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { ClockState, ErrorSeries } from "../../api/types";
import { ErrorChart } from "../ErrorChart/ErrorChart";
import { SimControls } from "../SimControls/SimControls";

const CLOCK_POLL_MS = 2000;
const ERROR_POLL_MS = 4000;

export function DemoTools({ appId, authorityKey }: { appId: string; authorityKey: string }) {
  const [clock, setClock] = useState<ClockState | null>(null);
  const [errors, setErrors] = useState<ErrorSeries | null>(null);
  const [demo, setDemo] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () =>
      api
        .debugClock()
        .then((c) => {
          if (!alive) return;
          setDemo(true);
          setClock(c);
        })
        .catch((e: unknown) => {
          if (!alive) return;
          setDemo(false);
          // 404 means DEMO_MODE is off; it won't turn on while the backend runs, so stop asking.
          if (e instanceof ApiError && e.status === 404) clearInterval(id);
        });
    const id = setInterval(poll, CLOCK_POLL_MS);
    void poll();
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!demo) return;
    let alive = true;
    const poll = () =>
      api
        .errorSeries(appId, authorityKey)
        .then((e) => alive && setErrors(e))
        .catch(() => undefined);
    void poll();
    const id = setInterval(poll, ERROR_POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [demo, appId, authorityKey]);

  if (!demo || !clock) return null;
  return (
    <section className="grid gap-4">
      <SimControls appId={appId} authorityKey={authorityKey} clock={clock} onChange={setClock} />
      <ErrorChart data={errors} />
    </section>
  );
}
