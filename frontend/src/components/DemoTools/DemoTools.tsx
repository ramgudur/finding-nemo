// DEMO_MODE tools for the authority console: SimControls + ErrorChart. Renders nothing when the server
// isn't in demo mode (/v1/debug/* is 404), so production consoles never show ground-truth data.
import { useEffect, useState } from "react";
import { api } from "../../api/client";
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
        .catch(() => alive && setDemo(false));
    void poll();
    const id = setInterval(poll, CLOCK_POLL_MS);
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
    <section style={{ display: "grid", gap: 12, borderTop: "1px solid #eee", paddingTop: 12 }}>
      <SimControls appId={appId} authorityKey={authorityKey} clock={clock} onChange={setClock} />
      <ErrorChart data={errors} />
    </section>
  );
}
