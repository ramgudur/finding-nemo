// End-user page: public instructions only. Must work on a phone over a tunnel.
// Sends only this device's own position; shows the guarded instruction and route; recomputes on each
// public stream tick. No key, no auth frame, no layers beyond what a public caller gets (rule 8): the
// only calls are the public /public (title, intent, sources), /map (area outline and, if the owner
// made it public, the score), /hotspots (only the public ones; the server filters per item),
// /instruction and the public stream. The instruction text is the server's, shown as sent.
// The camera fits the area once, then the first route once; after that only the user (or the
// Recenter button) moves it. Phones stack the page; from 768 px up the map fills the screen and
// the instruction card floats over it as a sheet on the left (the camera pads for it).
import L from "leaflet";
import { ChevronUp, Crosshair, Info, LocateFixed, MoveUpRight, Navigation, Phone, TriangleAlert } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import { openStream } from "../api/stream";
import type { CellCollection, GeoJsonBBox, Hotspot, Instruction, InstructionMode, MapResponse, PublicAppInfo } from "../api/types";
import { bboxToBounds, toLatLng } from "../components/Map/geo";
import { VectorFieldOverlay } from "../components/Map/VectorFieldLayer";
import { appDisplayName } from "../components/app/appName";

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
const RISK = "#E07A5F"; // soft warm coral: noticeable, not alarming

/** What a public caller may see of the risk: public hotspots, the score only if it is public, and how
 * many layers the public map carries at all (the server leaves out every authority-only one). */
type Risk = { hotspots: Hotspot[]; score: CellCollection | null; layers: number; vectors?: MapResponse["vectors"]; cellM?: number };

function riskOf(map: MapResponse | null, hotspots: Hotspot[]): Risk {
  return {
    hotspots: hotspots.filter((h) => h.visibility === "public"),
    score: map?.layers?.score ?? null,
    layers: Object.keys(map?.layers ?? {}).length,
    vectors: map?.vectors,
    cellM: map?.cell_m,
  };
}

/** /public lists one "N private source(s)" entry per kind (push, pull); say it once, summed. */
function sourcesLine(sources: PublicAppInfo["sources"] | undefined): string[] {
  let hidden = 0;
  const named: string[] = [];
  for (const s of sources ?? []) {
    const label = s.label.trim();
    const m = /^(\d+)\s+private\s+sources?$/i.exec(label);
    if (m) hidden += Number(m[1]);
    else if (label) named.push(label);
  }
  if (hidden > 0) named.push(`${hidden} private source${hidden === 1 ? "" : "s"}`);
  return named;
}

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

/** From this width up the map fills the screen and the card floats over it (Tailwind's md). */
const WIDE = "(min-width: 768px)";
const SHEET_GAP = 16; // the floating sheet's offset from the screen edge

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
  const heat = useRef<L.LayerGroup | null>(null);
  const riskGroup = useRef<L.LayerGroup | null>(null);
  const circles = useRef(new Map<string, L.Circle>());
  // camera: fit the area once, then the first route once; never again on its own
  const fitArea = useRef(false);
  const fitRoute = useRef(false);
  const lastFrameAt = useRef(0);
  const lastAsk = useRef(0);
  const watchId = useRef<number | null>(null);
  const slowTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const sheetRef = useRef<HTMLDivElement>(null);
  const [info, setInfo] = useState<PublicAppInfo | null>(null);
  const [infoDone, setInfoDone] = useState(false); // /public answered or failed (an older backend has none)
  const [lastTickAt, setLastTickAt] = useState<number | null>(null); // local ms of the last known tick
  const [bbox, setBbox] = useState<GeoJsonBBox | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [userCollapsed, setUserCollapsed] = useState<boolean | null>(null); // null: follow the default
  const [checkKey, setCheckKey] = useState(0); // bumped by "Check again"
  const [pos, setPos] = useState<Pos | null>(null);
  const [source, setSource] = useState<Source>(null);
  const [gps, setGps] = useState<Gps>(() => ("geolocation" in navigator ? "idle" : "unsupported"));
  const [ins, setIns] = useState<Instruction | null>(null);
  const [insAt, setInsAt] = useState(0); // when the instruction was computed, in local ms (see ask)
  const [netError, setNetError] = useState("");
  const [waitingFirstMap, setWaitingFirstMap] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [risk, setRisk] = useState<Risk | null>(null); // null: not known yet

  const live = phase === "loading" || phase === "active";
  // routing off: keep watching the public map (area and any public risk); only "not active" stops
  const watch = live || phase === "noDirections";
  const liveRef = useRef(live);
  liveRef.current = live;
  const bboxRef = useRef<GeoJsonBBox | null>(null);
  bboxRef.current = bbox;
  const outside = pos !== null && bbox !== null && !inside(bbox, pos);
  // Routing off and nothing public at all (no public layer, no public hotspot): the map would be an
  // empty outline, so the page says so in one line instead.
  const publicRisk = risk !== null && (risk.score !== null || risk.hotspots.length > 0);
  const nothingPublic = phase === "noDirections" && risk !== null && !publicRisk && risk.layers === 0;
  const showMap = live || (phase === "noDirections" && risk !== null && !nothingPublic);


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
      .catch(() => undefined)
      .finally(() => {
        if (!stop) setInfoDone(true);
      });
    return () => {
      stop = true;
    };
  }, [appId, checkKey]);

  // Area outline from the public map (bbox is always public). Right after activation /map is 404
  // "app has not ticked yet" for a tick or two, so retry with backoff until a bbox arrives (a stream
  // tick also carries it). Only a 404 that says the app doesn't exist ends the page.
  const haveBbox = bbox !== null;
  useEffect(() => {
    if (haveBbox || !watch) return;
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
  }, [appId, haveBbox, watch, checkKey]);

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
  // Both stop once the app is known not to be active; with no directions only the risk keeps updating
  // (ask() does nothing once the page isn't live).
  useEffect(() => {
    if (!watch) return;
    const close = openStream(appId, "", {
      onState: () => undefined,
      onFrame: (f) => {
        if (f.type !== "tick") return;
        setLastTickAt(Date.now());
        lastFrameAt.current = Date.now();
        if (!bboxRef.current && f.map?.bbox) setBbox(f.map.bbox);
        setRisk(riskOf(f.map ?? null, f.hotspots?.hotspots ?? []));
        if (posRef.current) void ask(posRef.current);
      },
    });
    // Risk on load from the public REST routes (no key), then from the stream ticks; REST again only
    // while the stream is quiet.
    let stop = false;
    const loadRisk = () => {
      Promise.all([api.map(appId).catch(() => null), api.hotspots(appId).catch(() => null)])
        .then(([m, h]) => {
          if (!stop && (m || h)) setRisk(riskOf(m, h?.hotspots ?? []));
        })
        .catch(() => undefined);
    };
    loadRisk();
    const id = setInterval(() => {
      if (posRef.current) void ask(posRef.current);
      if (Date.now() - lastFrameAt.current > STALE_MS) loadRisk();
    }, POLL_MS);
    return () => {
      stop = true;
      close();
      clearInterval(id);
    };
  }, [appId, ask, watch]);

  // a clock for the ages shown (1 s while directions are shown, else 15 s for the header)
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ins && live ? 1000 : 15_000);
    return () => clearInterval(id);
  }, [ins, live]);

  // map: area, you, route, destination
  useEffect(() => {
    if (!el.current || map.current) return;
    // zoom sits bottom right: top left is under the floating sheet on wide screens
    const m = L.map(el.current, { zoomControl: false, attributionControl: true });
    L.control.zoom({ position: "bottomright" }).addTo(m);
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
    // risk sits under the route and markers: heat, then circles, then the default overlay pane
    m.createPane("eu-heat").style.zIndex = "380";
    m.createPane("eu-risk").style.zIndex = "390";
    area.current = L.layerGroup().addTo(m);
    heat.current = L.layerGroup().addTo(m);
    riskGroup.current = L.layerGroup().addTo(m);
    overlay.current = L.layerGroup().addTo(m);
    map.current = m;
    const cs = circles.current;
    return () => {
      m.remove();
      map.current = null;
      cs.clear();
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

  // What "Recenter" frames: the route and you; else the area (and you, if you're outside it).
  const insRef = useRef<Instruction | null>(null);
  insRef.current = ins;
  const frame = useCallback((): L.LatLngBounds | null => {
    const b = bboxRef.current;
    const p = posRef.current;
    const r = insRef.current;
    const out = p !== null && b !== null && !inside(b, p);
    if (!out && r && r.route.length > 1) {
      const bounds = L.latLngBounds(r.route.map(([la, lo]) => toLatLng(la, lo)));
      if (p) bounds.extend(toLatLng(p.lat, p.lon));
      if (r.destination) bounds.extend(toLatLng(r.destination.lat, r.destination.lon));
      return bounds;
    }
    if (!b) return null;
    const bounds = bboxToBounds(b);
    if (out && p) bounds.extend(toLatLng(p.lat, p.lon));
    return bounds;
  }, []);

  // On wide screens the sheet covers the map's left side, so the camera frames what's beside it.
  const pad = useCallback((base: number): L.FitBoundsOptions => {
    const s = sheetRef.current;
    const left = s && window.matchMedia(WIDE).matches ? s.offsetWidth + SHEET_GAP + base : base;
    return { paddingTopLeft: [left, base], paddingBottomRight: [base, base] };
  }, []);

  const recenter = useCallback(() => {
    const m = map.current;
    const f = frame();
    if (m && f) m.fitBounds(f, pad(28));
  }, [frame, pad]);

  // First load: fit the area once (with you, if you're already outside it). Nothing refits later.
  useEffect(() => {
    const m = map.current;
    if (!m || !bbox || !showMap || fitArea.current) return; // a hidden map has no size to fit
    fitArea.current = true;
    m.invalidateSize();
    const f = frame();
    if (f) m.fitBounds(f, pad(12));
  }, [bbox, frame, pad, showMap]);

  // First route: fit route + you once. Later routes (a new tap, a tick) leave the camera alone.
  const hasRoute = !outside && ins !== null && ins.route.length > 1;
  useEffect(() => {
    if (!hasRoute || fitRoute.current) return;
    fitRoute.current = true;
    recenter();
  }, [hasRoute, recenter]);

  // Public high-risk areas: soft coral circles, opacity from the score; new ones fade in (opacity
  // only). Kept by id, so a tick updates them in place instead of re-flashing every 2 s.
  useEffect(() => {
    const g = riskGroup.current;
    if (!g) return;
    const seen = new Set<string>();
    for (const h of risk?.hotspots ?? []) {
      seen.add(h.id);
      const s = Math.max(0, Math.min(1, h.score));
      const style = { color: RISK, weight: 1.5, opacity: 0.35 + 0.35 * s, fillColor: RISK, fillOpacity: 0.12 + 0.23 * s };
      const radius = Math.max(h.radius_m, 1);
      const c = circles.current.get(h.id);
      if (c) {
        c.setLatLng(toLatLng(h.lat, h.lon));
        c.setRadius(radius);
        c.setStyle(style);
      } else {
        const n = L.circle(toLatLng(h.lat, h.lon), { ...style, radius, pane: "eu-risk", className: "eu-risk-in" });
        // a tap names it and still bubbles to the map, so you can set your position inside one
        n.bindTooltip("High risk", { direction: "top" });
        n.on("click", () => n.openTooltip());
        n.addTo(g);
        circles.current.set(h.id, n);
      }
    }
    for (const [id, c] of circles.current) {
      if (!seen.has(id)) {
        g.removeLayer(c);
        circles.current.delete(id);
      }
    }
  }, [risk]);

  // the public score, only if the owner made it public: a subtle heat wash under the circles
  const score = risk?.score ?? null;
  useEffect(() => {
    const g = heat.current;
    if (!g) return;
    g.clearLayers();
    if (!score || score.features.length === 0) return;
    L.geoJSON(score, {
      pane: "eu-heat",
      interactive: false,
      style: (f) => {
        const v = Math.max(0, Math.min(1, Number(f?.properties?.value) || 0));
        return { stroke: false, fillColor: RISK, fillOpacity: v < 0.15 ? 0 : 0.05 + 0.25 * v };
      },
    }).addTo(g);
  }, [score]);

  // the map div is hidden in the terminal states; Leaflet needs a resize when it shows again (and on
  // wide screens the first area fit waits for that, since the hidden map has no size)
  useEffect(() => {
    if (showMap && map.current) map.current.invalidateSize();
  }, [showMap]);

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
    if (pos && live) {
      L.circleMarker(toLatLng(pos.lat, pos.lon), { radius: 8, color: "#fff", fillColor: "#3b6895", fillOpacity: 1, weight: 3 })
        .bindTooltip("You", { direction: "bottom" })
        .addTo(g);
    }
  }, [ins, pos, outside, stale, live]);

  const recheck = () => {
    setNetError("");
    setWaitingFirstMap(false);
    setIns(null);
    setBbox(null);
    setInfo(null);
    setPhase("loading");
    setRisk(null);
    fitArea.current = false;
    fitRoute.current = false;
    setCheckKey((k) => k + 1);
    lastAsk.current = 0;
    if (posRef.current) setPos({ ...posRef.current }); // re-ask for the current position
  };

  // the server's title when /public answers, else the same rule applied to the id
  const title = info?.title || appDisplayName(appId) || "Safety alert";
  const sources = sourcesLine(info?.sources);
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
  } else if (nothingPublic) {
    panel = (
      <p aria-live="polite" className="m-0 text-[16px] leading-snug text-inkSoft">
        The organiser hasn't made this map public yet.
      </p>
    );
  } else if (phase === "noDirections") {
    panel = (
      <p aria-live="polite" className="m-0 text-[16px] leading-snug text-inkSoft">
        This alert doesn't give directions.{showMap ? " The map shows the area it covers." : ""}
      </p>
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
  // Routing on: known from /public, else (older backend) from the instruction route answering.
  const routingOn = phase !== "noDirections" && (info ? info.routing_enabled : phase === "active");
  const askForLocation = live && !pos && infoDone && (info ? info.routing_enabled : true);
  const showPosLegend = live && (routingOn || pos !== null);
  const hasLegend = showPosLegend || routingOn || publicRisk;

  // Phones: one stacked column (the sheet's children, then the map, then the legend, by flex order).
  // Wide screens with a map: the map fills the screen and the sheet floats over its left side.
  const wide = showMap;
  // The card folds to a title pill so it never has to cover the map. Without directions there is
  // nothing to act on, so it starts folded; with directions it starts open (it holds the location ask).
  const collapsed = showMap && (userCollapsed ?? (infoDone && !routingOn));
  return (
    <main
      className={`mx-auto flex max-w-[640px] flex-col gap-4 pb-[calc(16px+env(safe-area-inset-bottom))] pl-[max(16px,env(safe-area-inset-left))] pr-[max(16px,env(safe-area-inset-right))] pt-[max(16px,env(safe-area-inset-top))] text-[#1F3440] ${
        wide ? "md:relative md:block md:h-[100dvh] md:max-w-none md:overflow-hidden md:p-0" : ""
      }`}
    >
      {collapsed && (
        <div ref={sheetRef} className="order-1 self-start md:absolute md:left-4 md:top-4 md:z-[1100]">
          <h1 className="sr-only">{title}</h1>
          <button
            type="button"
            onClick={() => setUserCollapsed(false)}
            aria-expanded={false}
            aria-label={`About ${title}: show details`}
            title="Details"
            className="inline-flex h-11 w-11 items-center justify-center rounded-full bg-cream/95 text-primary shadow-[0_4px_18px_rgba(31,52,64,0.2)] backdrop-blur-sm transition-transform duration-150 ease-out active:scale-[0.97]"
          >
            <Info aria-hidden className="h-5 w-5" strokeWidth={2.25} />
          </button>
        </div>
      )}
      <div
        ref={collapsed ? undefined : sheetRef}
        className={
          collapsed
            ? "hidden"
            : wide
            ? "contents md:absolute md:left-4 md:top-4 md:z-[1100] md:flex md:max-h-[calc(100%-32px)] md:w-[380px] md:max-w-[calc(100%-32px)] md:flex-col md:gap-4 md:overflow-y-auto md:rounded-2xl md:bg-cream/95 md:p-5 md:shadow-[0_6px_28px_rgba(31,52,64,0.22)] md:backdrop-blur-sm"
            : "contents"
        }
      >
        {/* context: what this page is, whether it is live, and what data it uses */}
        <header className="order-1 grid gap-2">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h1 className="m-0 text-balance font-display text-[28px] font-semibold leading-tight tracking-[-0.015em] text-slate">{title}</h1>
              <p className="m-0 mt-0.5 font-mono text-[13px] text-inkSoft">{appId}</p>
            </div>
            {showMap && (
              <button
                type="button"
                onClick={() => setUserCollapsed(true)}
                aria-expanded={true}
                aria-label="Hide details and show the whole map"
                className="inline-flex min-h-[40px] shrink-0 items-center gap-1 rounded-xl px-2 text-[15px] font-bold text-primary transition-transform duration-150 ease-out active:scale-[0.97]"
              >
                Hide
                <ChevronUp aria-hidden className="h-[18px] w-[18px]" strokeWidth={2.25} />
              </button>
            )}
          </div>
          <p className="m-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-[16px] font-semibold text-inkSoft">
            <span className="inline-flex items-center gap-1.5 text-slate">
              <span aria-hidden className={`inline-block h-2.5 w-2.5 rounded-full ${status.dot}`} />
              {status.label}
            </span>
            {lastTickAt !== null && phase !== "inactive" && <span>{updatedAgo(now - lastTickAt)}</span>}
          </p>
          {sources.length > 0 && (
            <p className="m-0 text-[16px] leading-snug text-inkSoft">
              <span className="font-bold text-slate">Based on </span>
              {sources.join(" · ")}
            </p>
          )}
          {info?.intent && (
            <details className="text-[16px]">
              <summary className="-ml-1 inline-flex min-h-[44px] cursor-pointer list-none items-center gap-1.5 rounded px-1 font-bold text-primary [&::-webkit-details-marker]:hidden">
                <Info aria-hidden className="h-[18px] w-[18px]" strokeWidth={2.25} />
                <span className="underline underline-offset-4">About this alert</span>
              </summary>
              <p className="m-0 mt-1 max-w-[65ch] leading-snug text-[#2B4654]">{info.intent}</p>
            </details>
          )}
        </header>

        {askForLocation && (
          <section aria-live="polite" className="order-1 grid gap-3 rounded-xl bg-[#FBF9F3] px-4 py-4 shadow-[0_2px_10px_rgba(53,89,107,0.12)]">
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

        {panel && <div className="order-1">{panel}</div>}

        {showIns && (
          <p className={`order-1 m-0 -mt-2 text-[16px] ${stale ? "font-semibold text-danger" : "text-inkSoft"}`}>
            {stale ? `Directions not updated for ${duration(now - insAt)}.` : now - insAt < 5000 ? "Directions updated just now." : `Directions updated ${duration(now - insAt)} ago.`}
          </p>
        )}
        {live && netError && (
          <p role="status" className="order-1 m-0 text-[17px] font-semibold text-danger">
            {netError}
            {showIns ? " The directions above may be out of date." : ""}
          </p>
        )}

        {phase === "inactive" && (
          <button
            type="button"
            onClick={recheck}
            className="order-1 min-h-[48px] self-start rounded-xl border-2 border-slate bg-[#FBF9F3] px-5 text-[18px] font-bold text-slate"
          >
            Check again
          </button>
        )}

        {showMap && (hasLegend || (risk !== null && !publicRisk)) && (
          <div className="order-3 grid gap-2">
            {hasLegend && (
              <ul aria-label="Map legend" className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-[15px] font-semibold text-inkSoft">
                {showPosLegend && (
                  <li className="inline-flex items-center gap-1.5">
                    <span aria-hidden className="inline-block h-3 w-3 rounded-full border-2 border-white bg-[#3b6895] shadow-[0_0_0_1px_#3b6895]" />
                    your position
                  </li>
                )}
                {routingOn && (
                  <li className="inline-flex items-center gap-1.5">
                    <span aria-hidden className="inline-block h-1.5 w-5 rounded-full bg-[#3b6895]" />
                    route
                  </li>
                )}
                {publicRisk && (
                  <li className="inline-flex items-center gap-1.5">
                    <span aria-hidden className="inline-block h-3.5 w-3.5 rounded-full border" style={{ borderColor: RISK, background: `${RISK}59` }} />
                    high-risk area
                  </li>
                )}
                {routingOn && (
                  <li className="inline-flex items-center gap-1.5">
                    <span aria-hidden className="inline-block h-3 w-3 rounded-full border-2 border-[#0a6b37] bg-[#19a35b]" />
                    destination
                  </li>
                )}
              </ul>
            )}
            {risk !== null && !publicRisk && (
              <p className="m-0 text-[15px] leading-snug text-inkSoft">
                {routingOn ? "High-risk areas aren't shown on this public map, but your directions avoid them." : "High-risk areas aren't shown on this public map."}
              </p>
            )}
          </div>
        )}
        {live && pos && (
          <div className="order-3 grid gap-1">
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
      </div>

      <div hidden={!showMap} className="order-2 md:absolute md:inset-0">
        <div className="relative md:h-full">
          <div
            ref={el}
            aria-label={live ? "Map. Tap where you are to set your position." : "Map of the area this alert covers."}
            className="h-[min(60vh,480px)] min-h-[280px] w-full rounded-xl md:h-full md:min-h-0 md:rounded-none"
          />
          {/* public vector-field layers only: the server sends none the owner kept private (rule 8) */}
          <VectorFieldOverlay mapRef={map} vectors={risk?.vectors} bbox={bbox} cellM={risk?.cellM} pane="eu-vectors" zIndex={395} legendClassName="bottom-6 right-2.5 items-end" variant="public" />
          {bbox && (
            <button
              type="button"
              onClick={recenter}
              aria-label={hasRoute ? "Recenter on your route" : "Recenter on the area"}
              className="absolute right-2.5 top-2.5 z-[1000] inline-flex min-h-[44px] items-center gap-1.5 rounded-xl border border-slate/25 bg-[#FBF9F3] px-3 text-[16px] font-bold text-slate shadow-[0_1px_6px_rgba(31,52,64,0.18)] transition-transform duration-150 ease-out active:scale-[0.97]"
            >
              <Crosshair aria-hidden className="h-[18px] w-[18px]" strokeWidth={2.25} />
              Recenter
            </button>
          )}
        </div>
      </div>
    </main>
  );
}
