// What Nemotron just did, as map overlays (authority view only; rule 8):
//  - users with lowered trust at their last position (GET /trust/lowered, polled),
//  - a "change flash": on each new weight/trust Change, the score cells that moved between the tick
//    before and the tick after it, plus one chip ("Critic re-weighted · holdout +6%"),
//  - the old → new weight per input for ~10 s after a weight Change,
//  - which POIs are new since the previous poll (one pulse each).
// Nothing here fetches without a key, and a 401 drops the data at once.
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { AppSpec, BacktestDetail, Change, DistrustedUser, MapResponse, POI, WeightsView } from "../api/types";
import { cellKey, dominantInputs, sourceInputs, type SourceInput } from "../components/Map/sourceColours";

const TRUST_POLL_MS = 6000;
const MAP_BUFFER = 8; // ~16 s of ticks
const PENDING_GIVE_UP_MS = 12_000;
const CHIP_MS = 4500;
const GHOST_MS = 10_000;
/** A cell is outlined when its score moved by more than this (or its dominant source changed). */
export const FLASH_MIN_DELTA = 0.05;
const MAX_FLASH_CELLS = 300;

export type FlashTone = "warm" | "cool" | "shift";
export interface FlashCell {
  ring: number[][][];
  tone: FlashTone;
  /** For "shift": the new dominant input's hue. */
  hue?: string;
}
export interface Flash {
  id: number;
  cells: FlashCell[];
}
export interface ChangeChip {
  id: number;
  text: string;
  detail?: string;
  title?: string;
}
export interface WeightGhost {
  before: number;
  after: number;
}

export interface MapLearning {
  distrusted: DistrustedUser[];
  flash: Flash | null;
  chip: ChangeChip | null;
  /** input id → old/new summed weight, for ~10 s after a weight Change. */
  ghosts: Record<string, WeightGhost>;
  newPoiIds: ReadonlySet<string>;
  inputs: SourceInput[];
}

const ts = (iso: string) => Date.parse(iso);
/** A numeric field of a Change's before/after object, or null. */
const num = (o: unknown, k: string): number | null => {
  const v = o && typeof o === "object" ? (o as Record<string, unknown>)[k] : undefined;
  return typeof v === "number" && Number.isFinite(v) ? v : null;
};
const isWeightMove = (c: Change) => c.kind === "weight" && num(c.before, "weight") !== null && num(c.after, "weight") !== null;
const isTrustMove = (c: Change) => c.kind === "trust" && num(c.before, "trust") !== null && num(c.after, "trust") !== null;

/** "holdout +6%" relative to a baseline that isn't ~0, else the absolute score change. Null when unknown. */
export function holdoutText(bt: BacktestDetail | null): { text: string; title: string } | null {
  if (!bt || bt.holdout_delta === null || bt.holdout_baseline === null || bt.holdout_candidate === null) return null;
  const d = bt.holdout_delta;
  const method = (bt.deciding_method ?? "").replace(/_/g, " ");
  const title = `${method}: ${bt.holdout_baseline.toFixed(3)} → ${bt.holdout_candidate.toFixed(3)} on the held-out window`;
  const sign = d >= 0 ? "+" : "−";
  if (Math.abs(bt.holdout_baseline) >= 0.05) return { text: `holdout ${sign}${Math.round(Math.abs(d / bt.holdout_baseline) * 100)}%`, title };
  return { text: `holdout ${sign}${Math.abs(d).toFixed(2)}`, title };
}

function scores(m: MapResponse): Map<string, { v: number; ring: number[][][] }> {
  const out = new Map<string, { v: number; ring: number[][][] }>();
  for (const f of m.layers.score?.features ?? []) out.set(cellKey(f.properties.row, f.properties.col), { v: f.properties.value, ring: f.geometry.coordinates });
  return out;
}

export function useMapLearning(opts: {
  appId: string;
  authorityKey: string | null;
  spec: AppSpec | null;
  map: MapResponse | null;
  pois: POI[] | null;
  weights: WeightsView | null;
  changes: Change[];
  changesLoaded: boolean;
  /** Source mode is on: dominant-source changes are outlined too. */
  bySource: boolean;
  onRejected?: () => void;
}): MapLearning {
  const { appId, authorityKey: key, spec, map, pois, weights, changes, changesLoaded, bySource } = opts;
  const [distrusted, setDistrusted] = useState<DistrustedUser[]>([]);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [chip, setChip] = useState<ChangeChip | null>(null);
  const [ghosts, setGhosts] = useState<Record<string, WeightGhost>>({});
  const [newPoiIds, setNewPoiIds] = useState<ReadonlySet<string>>(new Set());
  const rejected = useRef(opts.onRejected);
  rejected.current = opts.onRejected;
  const inputs = useMemo(() => sourceInputs(key ? spec : null, weights), [key, spec, weights]);

  // --- lowered trust, polled -------------------------------------------------------------
  useEffect(() => {
    setDistrusted([]);
    if (!appId || !key) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const rows = await api.trustLowered(appId, key);
        if (alive) setDistrusted(rows);
      } catch (e) {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 401) {
          setDistrusted([]);
          rejected.current?.();
          return;
        }
        // 404 from an older backend without the route, or a blip: show nothing, keep trying
      }
      if (alive) timer = setTimeout(load, TRUST_POLL_MS);
    };
    void load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [appId, key]);

  // --- new POIs since the previous poll ----------------------------------------------------
  const seenPois = useRef<Set<string> | null>(null);
  useEffect(() => {
    seenPois.current = null;
    setNewPoiIds(new Set());
  }, [appId, key]);
  useEffect(() => {
    if (!pois) return;
    const ids = pois.filter((p) => p.status === "active").map((p) => p.id);
    const prev = seenPois.current;
    seenPois.current = new Set(ids);
    setNewPoiIds(prev ? new Set(ids.filter((id) => !prev.has(id))) : new Set()); // the first poll seeds, no pulse
  }, [pois]);

  // --- tick buffer ---------------------------------------------------------------------------
  const buffer = useRef<MapResponse[]>([]);
  useEffect(() => {
    buffer.current = [];
  }, [appId, key]);

  // --- change detection --------------------------------------------------------------------
  const seen = useRef<Set<string> | null>(null);
  const pending = useRef<{ changes: Change[]; at: number; weightsBefore: Record<string, number> } | null>(null);
  const flashSeq = useRef(0);
  const live = useRef({ inputs, weights, bySource });
  live.current = { inputs, weights, bySource };

  useEffect(() => {
    seen.current = null;
    pending.current = null;
    setFlash(null);
    setChip(null);
    setGhosts({});
  }, [appId, key]);

  /** Outline the cells that moved between the tick before and the tick after the change. */
  const resolve = () => {
    const p = pending.current;
    if (!p) return;
    const changeT = Math.max(...p.changes.map((c) => ts(c.t)));
    const buf = buffer.current;
    const before = [...buf].reverse().find((m) => ts(m.t) <= changeT) ?? null;
    const after = buf.find((m) => ts(m.t) > changeT) ?? null;
    if (!after) {
      if (Date.now() - p.at > PENDING_GIVE_UP_MS) pending.current = null;
      return;
    }
    pending.current = null;
    if (!before) return; // the change predates what this page has seen: chip only
    const { inputs: ins, weights: w, bySource: src } = live.current;
    const a = scores(after);
    const b = scores(before);
    const wNow = new Map((w?.layers ?? []).map((l) => [l.layer_id, l.weight]));
    const domA = src ? dominantInputs(after, ins, (id) => wNow.get(id) ?? 0) : null;
    const domB = src ? dominantInputs(before, ins, (id) => p.weightsBefore[id] ?? wNow.get(id) ?? 0) : null;
    const hue = new Map(ins.map((i) => [i.inputId, i.hue]));
    const cells: Array<FlashCell & { mag: number }> = [];
    for (const k of new Set([...a.keys(), ...b.keys()])) {
      const va = a.get(k)?.v ?? 0;
      const vb = b.get(k)?.v ?? 0;
      const ring = (a.get(k) ?? b.get(k))!.ring;
      const d = va - vb;
      if (Math.abs(d) > FLASH_MIN_DELTA) cells.push({ ring, tone: d > 0 ? "warm" : "cool", mag: Math.abs(d) });
      else if (domA && domB && a.has(k) && b.has(k) && domA.get(k) !== domB.get(k)) {
        const h = hue.get(domA.get(k) ?? "") ?? undefined;
        cells.push({ ring, tone: "shift", hue: h ?? undefined, mag: 0 });
      }
    }
    if (!cells.length) return;
    cells.sort((x, y) => y.mag - x.mag);
    setFlash({ id: ++flashSeq.current, cells: cells.slice(0, MAX_FLASH_CELLS).map(({ mag: _m, ...c }) => c) });
  };
  const resolveRef = useRef(resolve);
  resolveRef.current = resolve;

  useEffect(() => {
    if (!map || !map.layers.score) return;
    const buf = buffer.current;
    if (buf.length && buf[buf.length - 1]!.t === map.t) return;
    buf.push(map);
    if (buf.length > MAP_BUFFER) buf.shift();
    resolveRef.current();
  }, [map]);

  useEffect(() => {
    if (!key || !changesLoaded) return;
    if (seen.current === null) {
      seen.current = new Set(changes.map((c) => c.id)); // what was there when the page opened: no flash
      return;
    }
    const fresh = changes.filter((c) => !seen.current!.has(c.id));
    for (const c of fresh) seen.current.add(c.id);
    const moves = fresh.filter((c) => isWeightMove(c) || isTrustMove(c));
    if (!moves.length) return;

    // Weights before the change, per layer (for the "before" dominant source and the legend ghost).
    const wNow = Object.fromEntries((weights?.layers ?? []).map((l) => [l.layer_id, l.weight]));
    const weightsBefore: Record<string, number> = { ...wNow };
    for (const c of [...moves].reverse()) if (isWeightMove(c)) weightsBefore[c.target] = num(c.before, "weight")!;
    pending.current = { changes: moves, at: Date.now(), weightsBefore };
    resolveRef.current();

    const wMoves = moves.filter(isWeightMove);
    if (wMoves.length) {
      const next: Record<string, WeightGhost> = {};
      for (const inp of inputs) {
        if (!inp.layerIds.some((id) => wMoves.some((c) => c.target === id))) continue;
        const beforeSum = inp.layerIds.reduce((s, id) => s + (weightsBefore[id] ?? 0), 0);
        const afterSum = inp.layerIds.reduce((s, id) => s + (wNow[id] ?? 0), 0);
        if (Math.abs(afterSum - beforeSum) > 1e-6) next[inp.inputId] = { before: beforeSum, after: afterSum };
      }
      if (Object.keys(next).length) setGhosts(next);
    }

    // One chip: a weight move wins over trust moves in the same poll.
    const lead = wMoves[wMoves.length - 1] ?? moves[moves.length - 1]!;
    let text: string;
    if (isWeightMove(lead)) text = lead.reverts ? "Weight reverted" : lead.actor === "dev" ? "Weight changed by you" : "Critic re-weighted";
    else {
      const t = moves.filter(isTrustMove);
      const down = t.filter((c) => num(c.after, "trust")! < num(c.before, "trust")!);
      const users = new Set((down.length ? down : t).map((c) => c.target)).size;
      text = `Trust ${down.length ? "lowered" : "raised"} for ${users} user${users === 1 ? "" : "s"}`;
    }
    const id = ++flashSeq.current;
    setChip({ id, text });
    if (lead.backtest_id && key) {
      api.backtest(appId, key, lead.backtest_id).then(
        (bt) => {
          const h = holdoutText(bt);
          if (h) setChip((c) => (c && c.id === id ? { ...c, detail: h.text, title: h.title } : c));
        },
        () => undefined, // no backtest detail: the chip stays as it is
      );
    }
    // weights and inputs are read at the moment of the poll on purpose
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [changes, changesLoaded, key]);

  // timers: chip, ghosts and the flash clear themselves
  useEffect(() => {
    if (!chip) return;
    const id = chip.id;
    const t = setTimeout(() => setChip((c) => (c?.id === id ? null : c)), CHIP_MS);
    return () => clearTimeout(t);
    // the timer runs from when the chip appeared, not from when its holdout detail arrived
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chip?.id]);
  useEffect(() => {
    if (!Object.keys(ghosts).length) return;
    const t = setTimeout(() => setGhosts({}), GHOST_MS);
    return () => clearTimeout(t);
  }, [ghosts]);

  return { distrusted: key ? distrusted : [], flash, chip, ghosts, newPoiIds, inputs };
}
