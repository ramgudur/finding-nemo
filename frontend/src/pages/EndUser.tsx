// End-user page: public instructions only. Must work on a phone over a tunnel.
// Sends only this device's own position; shows the guarded instruction and route; recomputes on each
// public stream tick. No key, no auth frame, no layers beyond what a public caller gets (rule 8): the
// only calls are the public /public (title, intent, sources), /map (for the area outline),
// /instruction and the public stream. The instruction text is the server's, shown as sent.
import L from "leaflet";
import { Info, LocateFixed, MoveUpRight, Navigation, Phone, TriangleAlert } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import { openStream } from "../api/stream";
import type { GeoJsonBBox, Instruction, InstructionMode, PublicAppInfo } from "../api/types";
import { bboxToBounds, toLatLng } from "../components/Map/geo";

type Pos = { lat: number; lon: number };
type Source = "gps" | "map" | null;
/** loading: no answer yet; active: the app answers; inactive / noDirections: terminal, nothing polls. */
type Phase = "loading" | "active" | "inactive" | "noDirections";
/** idle: not asked yet (the browser prompt only appears after the user taps "Share your location"). */
type Gps = "idle" | "finding" | "slow" | "off" | "unsupported" | "ok";

const MIN_RECOMPUTE_MS = 2000; // the engine ticks every 2 s
const POLL_MS = 5000;
const STALE_MS = 10_000;
const GPS_SLOW_MS = 5000;
const MAP_RETRY_MAX_MS = 10_000;

/** A 404's detail tells a real "not active" / "routing off" apart from "not ticked yet" (transient). */
function terminal404(e: unknown): Phase | null {
  if (!(e instanceof ApiError) || e.status !== 404) return null;
  if (/does not give instructions/i.test(e.message)) return "noDirections";
  if (/not found|not active/i.test(e.message)) return "inactive";
  return null;
}

function inside(b: GeoJsonBBox, p: Pos): boolean {
  const [minLon, minLat, maxLon, maxLat] = b;
  return p.lat >= minLat && p.lat <= maxLat && p.lon >= minLon && p.lon <= maxLon;
}

function duration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.round(m / 60)} h`;
}

/** "Updated just now" / "Updated 4 min ago", for the data behind the whole page. */
function updatedAgo(ms: number): string {
  if (ms < 60_000) return "Updated just now";
  return `Updated ${duration(ms)} ago`;
}

function distanceText(m: number): string {
  if (!Number.isFinite(m) || m <= 0) return "";
  if (m < 1000) return `${Math.max(10, Math.round(m / 10) * 10)} m`;
  return `${(m / 1000).toFixed(m < 10_000 ? 1 : 0)} km`;
}

/** Same rule as the server's PublicAppInfo.title, for an older backend without /public. */
function titleFromId(appId: string): string {
  return appId
    .split(/[-_]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function modeOf(ins: Instruction): InstructionMode {
  if (ins.mode) return ins.mode;
  return ins.destination === null ? "fallback" : "route"; // older backend: no mode field
}

const GUIDE: Record<InstructionMode, { heading: string; icon: ReactNode; card: string; head: string }> = {
  route: {
    heading: "Your safest way out",
    icon: <Navigation aria-hidden className="h-6 w-6 shrink-0" strokeWidth={2.25} />,
    card: "border-primary bg-[#FBF9F3] text-[#1F3440]",
    head: "text-primary",
  },
  escape: {
    heading: "Leave this area now",
    icon: <TriangleAlert aria-hidden className="h-6 w-6 shrink-0" strokeWidth={2.25} />,
    card: "border-[#B86E00] bg-[#FFF2DE] text-[#3F2800]",
    head: "text-[#7A4700]",
  },
  nearest_safe: {
    heading: "Move out of the high-risk area",
    icon: <MoveUpRight aria-hidden className="h-6 w-6 shrink-0" strokeWidth={2.25} />,
    card: "border-[#B86E00] bg-[#FFF7EA] text-[#3F2800]",
    head: "text-[#7A4700]",
  },
  fallback: {
    heading: "No safe route found right now",
    icon: <Info aria-hidden className="h-6 w-6 shrink-0" strokeWidth={2.25} />,
    card: "border-slate bg-[#FBF9F3] text-[#1F3440]",
    head: "text-slate",
  },
};

export function EndUser({ appId }: { appId: string }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const overlay = useRef<L.LayerGroup | null>(null);
  const area = useRef<L.LayerGroup | null>(null);
  const lastAsk = useRef(0);
  const watchId = useRef<number | null>(null);
  const slowTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const intentRef = useRef<HTMLParagraphElement>(null);
  const [info, setInfo] = useState<PublicAppInfo | null>(null);
  const [intentOpen, setIntentOpen] = useState(false);
  const [intentClamped, setIntentClamped] = useState(false);
  const [lastTickAt, setLastTickAt] = useState<number | null>(null); // local ms of the last known tick
  const [bbox, setBbox] = useState<GeoJsonBBox | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [checkKey, setCheckKey] = useState(0); // bumped by "Check again"
  const [pos, setPos] = useState<Pos | null>(null);
  const [source, setSource] = useState<Source>(null);
  const [gps, setGps] = useState<Gps>(() => ("geolocation" in navigator ? "idle" : "unsupported"));
  const [ins, setIns] = useState<Instruction | null>(null);
  const [insAt, setInsAt] = useState(0); // when the instruction was computed, in local ms (see ask)
  const [netError, setNetError] = useState("");
  const [waitingFirstMap, setWaitingFirstMap] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const live = phase === "loading" || phase === "active";
  const liveRef = useRef(live);
  liveRef.current = live;
  const bboxRef = useRef<GeoJsonBBox | null>(null);
  bboxRef.current = bbox;
  const outside = pos !== null && bbox !== null && !inside(bbox, pos);

  // Public app info: who is asking you to do what, and from which data. Optional: on any failure
  // (an older backend without /public, or a network blip) the page works as before from /map and
  // /instruction, with a title derived from the app id.
  useEffect(() => {
    let stop = false;
    api
      .publicInfo(appId)
      .then((i) => {
        if (stop) return;
        setInfo(i);
        const b = i.bbox;
        if (b) setBbox((cur) => cur ?? [b.min_lon, b.min_lat, b.max_lon, b.max_lat]);
        const t = i.last_update ? Date.parse(i.last_update) : NaN;
        // a future time (clock skew or a sim clock) says nothing honest about age: leave it unknown
        if (Number.isFinite(t) && t <= Date.now() + 60_000) setLastTickAt((cur) => cur ?? Math.min(t, Date.now()));
        if (i.status !== "active") setPhase("inactive");
        else if (!i.routing_enabled) setPhase("noDirections");
      })
      .catch(() => undefined);
    return () => {
      stop = true;
    };
  }, [appId, checkKey]);

  // does the intent overflow its two-line clamp? (only then offer "more")
  useLayoutEffect(() => {
    const p = intentRef.current;
    if (!p || intentOpen) return;
    const check = () => setIntentClamped(p.scrollHeight > p.clientHeight + 1);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, [info?.intent, intentOpen]);

  // Area outline from the public map (bbox is always public). Right after activation /map is 404
  // "app has not ticked yet" for a tick or two, so retry with backoff until a bbox arrives (a stream
  // tick also carries it). Only a 404 that says the app doesn't exist ends the page.
  const haveBbox = bbox !== null;
  useEffect(() => {
    if (haveBbox || !live) return;
    let stop = false;
    let delay = 1000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tryOnce = async () => {
      try {
        const m = await api.map(appId);
        if (!stop) setBbox(m.bbox);
      } catch (e) {
        if (stop) return;
        const t = terminal404(e);
        if (t) {
          setPhase(t);
          return;
        }
        timer = setTimeout(() => void tryOnce(), delay);
        delay = Math.min(delay * 2, MAP_RETRY_MAX_MS);
      }
    };
    void tryOnce();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [appId, haveBbox, live, checkKey]);

  // Phone GPS, only once the user asks (or has already allowed it); tapping the map sets the position too.
  const startGps = useCallback(() => {
    if (!("geolocation" in navigator)) {
      setGps("unsupported");
      return;
    }
    if (watchId.current !== null) navigator.geolocation.clearWatch(watchId.current);
    clearTimeout(slowTimer.current);
    setGps((g) => (g === "ok" ? g : "finding"));
    slowTimer.current = setTimeout(() => setGps((g) => (g === "finding" ? "slow" : g)), GPS_SLOW_MS);
    watchId.current = navigator.geolocation.watchPosition(
      (p) => {
        setPos({ lat: p.coords.latitude, lon: p.coords.longitude });
        setSource("gps");
        setGps("ok");
      },
      (err) => setGps((g) => (g === "ok" ? g : err.code === err.PERMISSION_DENIED ? "off" : "slow")),
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 },
    );
  }, []);

  useEffect(() => {
    let stop = false;
    // already allowed on an earlier visit: no prompt will show, so start right away
    navigator.permissions
      ?.query({ name: "geolocation" as PermissionName })
      .then((st) => {
        if (!stop && st.state === "granted") startGps();
      })
      .catch(() => undefined);
    return () => {
      stop = true;
      clearTimeout(slowTimer.current);
      if (watchId.current !== null) navigator.geolocation.clearWatch(watchId.current);
      watchId.current = null;
    };
  }, [startGps]);

  const posRef = useRef<Pos | null>(null);
  posRef.current = pos;

  const ask = useCallback(
    async (p: Pos, force = false) => {
      if (!liveRef.current) return;
      const b = bboxRef.current;
      if (b && !inside(b, p)) return; // outside the grid: the page says so; nothing to route
      const t0 = Date.now();
      if (!force && t0 - lastAsk.current < MIN_RECOMPUTE_MS) return;
      lastAsk.current = t0;
      try {
        const next = await api.instruction(appId, p);
        const cur = posRef.current;
        const bb = bboxRef.current;
        if (cur && bb && !inside(bb, cur)) return; // moved out of the area while this was in flight
        // Age comes from computed_at; if the server clock is far from ours (skew, or a sim clock),
        // use the arrival time instead so the age shown stays honest.
        const at = Date.parse(next.computed_at);
        const got = Date.now();
        setIns(next);
        setInsAt(Number.isFinite(at) && Math.abs(got - at) < 60_000 ? at : got);
        setNow(got);
        setPhase("active");
        setNetError("");
        setWaitingFirstMap(false);
      } catch (e) {
        const t = terminal404(e);
        if (t) {
          setPhase(t);
          setIns(null);
        } else if (e instanceof ApiError && e.status === 503) {
          setWaitingFirstMap(true);
          setNetError("The service is busy. Retrying…");
        } else {
          setNetError("Can't reach the service. Retrying…");
        }
      }
    },
    [appId],
  );

  useEffect(() => {
    if (pos) void ask(pos, true);
  }, [pos, ask]);

  // recompute on every public tick (the hazard moves); keep a slow poll in case the stream is down.
  // Both stop once the app is known not to be active (or gives no directions).
  useEffect(() => {
    if (!live) return;
    const close = openStream(appId, "", {
      onState: () => undefined,
      onFrame: (f) => {
        if (f.type !== "tick") return;
        setLastTickAt(Date.now());
        if (!bboxRef.current && f.map?.bbox) setBbox(f.map.bbox);
        if (posRef.current) void ask(posRef.current);
      },
    });
    const id = setInterval(() => posRef.current && void ask(posRef.current), POLL_MS);
    return () => {
      close();
      clearInterval(id);
    };
  }, [appId, ask, live]);

  // a clock for the ages shown (1 s while directions are shown, else 15 s for the header)
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ins && live ? 1000 : 15_000);
    return () => clearInterval(id);
  }, [ins, live]);

  // map: area, you, route, destination
  useEffect(() => {
    if (!el.current || map.current) return;
    const m = L.map(el.current, { zoomControl: true, attributionControl: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 20,
      maxNativeZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(m);
    m.setView([0, 0], 2);
    m.on("click", (e: L.LeafletMouseEvent) => {
      if (!liveRef.current) return;
      setPos({ lat: e.latlng.lat, lon: e.latlng.lng });
      setSource("map");
    });
    area.current = L.layerGroup().addTo(m);
    overlay.current = L.layerGroup().addTo(m);
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    const g = area.current;
    if (!m || !g) return;
    g.clearLayers();
    if (!bbox) return;
    // interactive: false so a tap inside the outline still reaches the map's click handler
    L.rectangle(bboxToBounds(bbox), { color: "#35596b", weight: 2, dashArray: "6 6", fill: false, interactive: false }).addTo(g);
  }, [bbox]);

  // outside the area: show both the area and where the user is, so they can see which way it lies
  useEffect(() => {
    const m = map.current;
    const p = posRef.current;
    if (!m || !bbox) return;
    if (outside && p) m.fitBounds(bboxToBounds(bbox).extend(toLatLng(p.lat, p.lon)), { padding: [24, 24] });
    else m.fitBounds(bboxToBounds(bbox), { padding: [12, 12] });
  }, [outside, bbox]);

  // the map div is hidden in the terminal states; Leaflet needs a resize when it shows again
  useEffect(() => {
    if (live && map.current) map.current.invalidateSize();
  }, [live]);

  const stale = ins !== null && now - insAt > STALE_MS;
  useEffect(() => {
    const g = overlay.current;
    if (!g) return;
    g.clearLayers();
    const shown = outside ? null : ins;
    if (shown && shown.route.length > 1) {
      L.polyline(
        shown.route.map(([la, lo]) => toLatLng(la, lo)),
        { color: "#3b6895", weight: 6, opacity: stale ? 0.45 : 0.9 },
      ).addTo(g);
    }
    if (shown?.destination) {
      L.circleMarker(toLatLng(shown.destination.lat, shown.destination.lon), {
        radius: 10,
        color: "#0a6b37",
        fillColor: "#19a35b",
        fillOpacity: stale ? 0.5 : 1,
        weight: 3,
      })
        .bindTooltip(shown.destination.label, { permanent: true, direction: "top" })
        .addTo(g);
    }
    if (pos) {
      L.circleMarker(toLatLng(pos.lat, pos.lon), { radius: 8, color: "#fff", fillColor: "#3b6895", fillOpacity: 1, weight: 3 })
        .bindTooltip("You", { direction: "bottom" })
        .addTo(g);
    }
  }, [ins, pos, outside, stale]);

  const recheck = () => {
    setNetError("");
    setWaitingFirstMap(false);
    setIns(null);
    setBbox(null);
    setInfo(null);
    setPhase("loading");
    setCheckKey((k) => k + 1);
    lastAsk.current = 0;
    if (posRef.current) setPos({ ...posRef.current }); // re-ask for the current position
  };

  const title = info?.title || titleFromId(appId) || "Safety alert";
  const sources = info?.sources.map((s) => s.label.trim()).filter(Boolean) ?? [];
  const status: { label: string; dot: string } =
    phase === "inactive"
      ? { label: "Not active", dot: "bg-[#8199A0]" }
      : phase === "loading" && !info
        ? { label: "Checking…", dot: "bg-[#8199A0]" }
        : { label: "Live", dot: "bg-[#1F8A52]" };

  const gpsLine =
    gps === "unsupported"
      ? "This browser can't share location. Tap the map where you are."
      : gps === "off"
        ? "Location is off for this site. Tap the map where you are, or allow location and try again."
        : gps === "slow"
          ? "Still looking for your position… you can also tap the map."
          : gps === "finding"
            ? "Finding your position…"
            : "";

  // ---- main panel: one clear state at a time ----
  let panel: ReactNode = null;
  if (phase === "inactive") {
    panel = (
      <section aria-live="polite" className="rounded-xl border-2 border-slate/40 bg-cream px-4 py-4 text-slate">
        <h2 className="m-0 text-[20px] font-bold leading-snug">This alert is not active right now.</h2>
        <p className="m-0 mt-1 text-[18px] leading-snug">There are no directions to show. Check again later.</p>
      </section>
    );
  } else if (phase === "noDirections") {
    panel = (
      <section aria-live="polite" className="rounded-xl border-2 border-slate/40 bg-cream px-4 py-4 text-slate">
        <h2 className="m-0 text-[20px] font-bold leading-snug">This alert doesn't give directions.</h2>
        <p className="m-0 mt-1 text-[18px] leading-snug">The map shows the area it covers.</p>
      </section>
    );
  } else if (outside) {
    panel = (
      <section aria-live="polite" className="rounded-xl border-2 border-[#B86E00] bg-[#FFF2DE] px-4 py-4 text-[#3F2800]">
        <h2 className="m-0 text-[20px] font-bold leading-snug">You're outside this alert's area.</h2>
        <p className="m-0 mt-1 text-[18px] leading-snug">The dashed outline on the map shows the area it covers.</p>
      </section>
    );
  } else if (ins) {
    const mode = modeOf(ins);
    const g = GUIDE[mode];
    const walk = typeof ins.walk_minutes === "number" && ins.walk_minutes > 0 ? `≈ ${ins.walk_minutes} min walk` : "";
    const dist = mode === "fallback" ? "" : distanceText(ins.distance_m);
    panel = (
      <section
        aria-live="polite"
        className={`rounded-xl border-2 px-4 py-4 shadow-[0_2px_10px_rgba(53,89,107,0.12)] ${g.card} ${stale ? "opacity-75" : ""}`}
      >
        {stale && <p className="m-0 mb-1 text-[15px] font-bold uppercase tracking-wide">Last known directions</p>}
        <h2 className={`m-0 flex items-center gap-2 text-[22px] font-extrabold leading-tight ${g.head}`}>
          {g.icon}
          {g.heading}
        </h2>
        <p className="m-0 mt-2 text-[20px] leading-snug">{ins.text}</p>
        {(walk || dist) && (
          <p className="m-0 mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[18px] font-bold tabular-nums">
            {walk && <span>{walk}</span>}
            {dist && <span>{dist}</span>}
          </p>
        )}
        {mode === "nearest_safe" && (
          <p className="m-0 mt-2 text-[17px] leading-snug">
            The nearest named safe place is far away, so this leads to the closest ground clear of danger.
          </p>
        )}
        {mode === "fallback" && (
          <p className="m-0 mt-3 flex items-start gap-2 text-[18px] font-semibold leading-snug">
            <Phone aria-hidden className="mt-0.5 h-5 w-5 shrink-0" />
            If you are in danger, call your local emergency services.
          </p>
        )}
      </section>
    );
  } else if (pos) {
    panel = (
      <section aria-live="polite" className="rounded-xl border-2 border-primary/50 bg-[#FBF9F3] px-4 py-4 text-[20px] text-[#1F3440]">
        {waitingFirstMap ? "Waiting for the first map…" : "Getting directions…"}
      </section>
    );
  }

  const showIns = live && ins !== null && !outside;
  const askForLocation = live && !pos;

  return (
    <main className="mx-auto grid max-w-[640px] gap-4 pb-[calc(16px+env(safe-area-inset-bottom))] pl-[max(16px,env(safe-area-inset-left))] pr-[max(16px,env(safe-area-inset-right))] pt-[max(16px,env(safe-area-inset-top))] text-[#1F3440]">
      {/* context: what this page is, whether it is live, and what data it uses */}
      <header className="grid gap-2">
        <h1 className="m-0 text-balance text-[28px] font-extrabold leading-tight tracking-[-0.02em] text-slate">{title}</h1>
        <p className="m-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-[16px] font-semibold text-inkSoft">
          <span className="inline-flex items-center gap-1.5 text-slate">
            <span aria-hidden className={`inline-block h-2.5 w-2.5 rounded-full ${status.dot}`} />
            {status.label}
          </span>
          {lastTickAt !== null && phase !== "inactive" && <span>{updatedAgo(now - lastTickAt)}</span>}
        </p>
        {info?.intent && (
          <div>
            <p
              ref={intentRef}
              id="enduser-intent"
              className={`m-0 max-w-[65ch] text-[18px] leading-snug text-[#2B4654] ${intentOpen ? "" : "line-clamp-2"}`}
            >
              {info.intent}
            </p>
            {(intentClamped || intentOpen) && (
              <button
                type="button"
                aria-expanded={intentOpen}
                aria-controls="enduser-intent"
                onClick={() => setIntentOpen((o) => !o)}
                className="-ml-1 min-h-[44px] rounded px-1 text-[17px] font-bold text-primary underline underline-offset-4"
              >
                {intentOpen ? "less" : "more"}
              </button>
            )}
          </div>
        )}
        {sources.length > 0 && (
          <p className="m-0 text-[16px] leading-snug text-inkSoft">
            <span className="font-bold text-slate">Based on: </span>
            {sources.join(" · ")}
          </p>
        )}
      </header>

      {askForLocation && (
        <section aria-live="polite" className="grid gap-3 rounded-xl bg-[#FBF9F3] px-4 py-4 shadow-[0_2px_10px_rgba(53,89,107,0.12)]">
          <h2 className="m-0 text-[22px] font-extrabold leading-tight text-slate">Share your location to get the safest way out</h2>
          {gps !== "unsupported" && (
            <button
              type="button"
              onClick={startGps}
              disabled={gps === "finding"}
              className="inline-flex min-h-[52px] items-center justify-center gap-2 rounded-xl bg-primary px-5 text-[19px] font-bold text-cream transition-colors hover:bg-primaryHover disabled:cursor-progress disabled:opacity-80"
            >
              <LocateFixed aria-hidden className="h-5 w-5" strokeWidth={2.5} />
              {gps === "finding" || gps === "slow" ? "Finding your position…" : gps === "off" ? "Try location again" : "Share my location"}
            </button>
          )}
          <p className="m-0 text-center text-[18px] font-semibold text-slate">or tap the map where you are</p>
          {gpsLine && gps !== "finding" && (
            <p role="status" className="m-0 text-[17px] font-semibold leading-snug text-danger">
              {gpsLine}
            </p>
          )}
          <p className="m-0 text-[16px] leading-snug text-inkSoft">
            Only your position is sent, once, to compute your route. It isn't stored with your identity.
          </p>
        </section>
      )}

      {panel}

      {showIns && (
        <p className={`m-0 -mt-2 text-[16px] ${stale ? "font-semibold text-danger" : "text-inkSoft"}`}>
          {stale ? `Directions not updated for ${duration(now - insAt)}.` : now - insAt < 5000 ? "Directions updated just now." : `Directions updated ${duration(now - insAt)} ago.`}
        </p>
      )}
      {live && netError && (
        <p role="status" className="m-0 text-[17px] font-semibold text-danger">
          {netError}
          {showIns ? " The directions above may be out of date." : ""}
        </p>
      )}

      {!live && (
        <button
          type="button"
          onClick={recheck}
          className="min-h-[48px] justify-self-start rounded-xl border-2 border-slate bg-[#FBF9F3] px-5 text-[18px] font-bold text-slate"
        >
          Check again
        </button>
      )}

      <div
        ref={el}
        hidden={!live}
        aria-label="Map. Tap where you are to set your position."
        className="w-full rounded-xl"
        style={{ height: "min(60vh, 480px)", minHeight: 280 }}
      />
      {live && pos && (
        <div className="grid gap-1">
          <p className="m-0 text-[16px] leading-snug text-inkSoft">
            {source === "gps" ? "Using your phone's location." : "Using the spot you tapped."} Tap the map to change it. Only your
            own position is sent; it isn't stored with your identity.
          </p>
          {source === "map" && gps !== "unsupported" && gps !== "ok" && (
            <button
              type="button"
              onClick={startGps}
              className="inline-flex min-h-[44px] items-center gap-2 justify-self-start text-[17px] font-bold text-primary underline underline-offset-4"
            >
              <LocateFixed aria-hidden className="h-5 w-5" />
              {gps === "finding" || gps === "slow" ? "Finding your position…" : "Use my location instead"}
            </button>
          )}
        </div>
      )}
    </main>
  );
}
