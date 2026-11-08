// End-user page: public instructions only. Must work on a phone over a tunnel.
// Sends only this device's own position; shows the guarded instruction and route; recomputes on each
// public stream tick. No key, no auth frame, no layers beyond what a public caller gets (rule 8): the
// only calls are the public /map (for the area outline), /instruction and the public stream.
import L from "leaflet";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import { openStream } from "../api/stream";
import type { GeoJsonBBox, Instruction } from "../api/types";
import { bboxToBounds, toLatLng } from "../components/Map/geo";

type Pos = { lat: number; lon: number };
type Source = "gps" | "map" | null;
/** loading: no answer yet; active: the app answers; inactive / noDirections: terminal, nothing polls. */
type Phase = "loading" | "active" | "inactive" | "noDirections";
type Gps = "finding" | "slow" | "off" | "unsupported" | "ok";

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

export function EndUser({ appId }: { appId: string }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const overlay = useRef<L.LayerGroup | null>(null);
  const area = useRef<L.LayerGroup | null>(null);
  const lastAsk = useRef(0);
  const [bbox, setBbox] = useState<GeoJsonBBox | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [checkKey, setCheckKey] = useState(0); // bumped by "Check again"
  const [pos, setPos] = useState<Pos | null>(null);
  const [source, setSource] = useState<Source>(null);
  const [gps, setGps] = useState<Gps>("finding");
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

  // phone GPS; tapping the map sets the position too (desktop, or GPS denied)
  useEffect(() => {
    if (!("geolocation" in navigator)) {
      setGps("unsupported");
      return;
    }
    const slow = setTimeout(() => setGps((g) => (g === "finding" ? "slow" : g)), GPS_SLOW_MS);
    const id = navigator.geolocation.watchPosition(
      (p) => {
        setPos({ lat: p.coords.latitude, lon: p.coords.longitude });
        setSource("gps");
        setGps("ok");
      },
      (err) => setGps((g) => (g === "ok" ? g : err.code === err.PERMISSION_DENIED ? "off" : "slow")),
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 },
    );
    return () => {
      clearTimeout(slow);
      navigator.geolocation.clearWatch(id);
    };
  }, []);

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
          setNetError("The service is busy (503). Retrying…");
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

  // a 1 s clock for the instruction's age
  useEffect(() => {
    if (!ins || !live) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
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
    setPhase("loading");
    setCheckKey((k) => k + 1);
    lastAsk.current = 0;
    if (posRef.current) setPos({ ...posRef.current }); // re-ask for the current position
  };

  const gpsStatus =
    gps === "unsupported"
      ? "Tap the map where you are."
      : gps === "off"
        ? "Location is off. Tap the map where you are."
        : gps === "slow"
          ? "Waiting for location… or tap the map where you are."
          : "Finding your position…";

  // main panel: one clear state at a time
  let tone: "info" | "warn" | "muted" = "info";
  let body: ReactNode;
  if (phase === "inactive") {
    tone = "muted";
    body = (
      <>
        <strong className="block">This alert is not active.</strong>
        <span className="mt-1 block text-[17px]">There are no directions to show right now.</span>
      </>
    );
  } else if (phase === "noDirections") {
    tone = "muted";
    body = <strong className="block">This app doesn't give directions.</strong>;
  } else if (outside) {
    tone = "warn";
    body = (
      <>
        <strong className="block">You're outside this alert's area.</strong>
        <span className="mt-1 block text-[17px]">The dashed outline on the map shows the area it covers.</span>
      </>
    );
  } else if (ins) {
    tone = ins.destination === null ? "warn" : "info";
    body = (
      <>
        {stale && <span className="mb-1 block text-[15px] font-bold uppercase tracking-wide">Last known:</span>}
        {ins.text}
      </>
    );
  } else if (pos && waitingFirstMap) {
    body = "Waiting for the first map…";
  } else if (pos) {
    body = "Getting directions…";
  } else {
    body = gpsStatus;
  }

  const panel =
    tone === "warn"
      ? "border-[#b86e00] bg-[#fff4e5] text-[#4a2e00]"
      : tone === "muted"
        ? "border-slate bg-cream text-slate"
        : "border-primary bg-white text-[#1f3440]";
  const showIns = live && ins !== null && !outside;

  return (
    <div className="mx-auto grid max-w-[640px] gap-3 px-4 pb-[calc(16px+env(safe-area-inset-bottom))] pt-4">
      <section
        aria-live="polite"
        className={`rounded-lg border-2 px-4 py-3.5 text-[20px] leading-snug ${panel} ${showIns && stale ? "opacity-70" : ""}`}
      >
        {body}
      </section>

      {showIns && (
        <p className={`m-0 text-[15px] ${stale ? "font-semibold text-danger" : "text-inkSoft"}`}>
          {stale ? `Not updated for ${duration(now - insAt)}.` : `Updated ${duration(now - insAt)} ago.`}
        </p>
      )}
      {live && netError && (
        <p role="status" className="m-0 text-[16px] font-semibold text-danger">
          {netError}
          {showIns ? " The directions above may be out of date." : ""}
        </p>
      )}

      {!live && (
        <button
          type="button"
          onClick={recheck}
          className="min-h-[48px] justify-self-start rounded-lg border-2 border-slate bg-white px-5 text-[17px] font-semibold text-slate"
        >
          Check again
        </button>
      )}

      <div
        ref={el}
        hidden={!live}
        aria-label="Map. Tap where you are to set your position."
        className="w-full rounded-lg"
        style={{ height: "min(60vh, 480px)", minHeight: 280 }}
      />
      {live && (
        <p className="m-0 text-[15px] leading-snug text-inkSoft">
          {source === "gps" ? "Using your phone's location. " : source === "map" ? "Using the spot you tapped. " : ""}
          Tap the map to set your position. Only your own position is sent; it isn't stored with your name.
        </p>
      )}
    </div>
  );
}
