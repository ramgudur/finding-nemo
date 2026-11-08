// Shared builder state across the /new steps and the app pages. The DRAFT (what the dev typed: name, app id,
// intent, area, destinations, sources) persists to sessionStorage so a refresh keeps it; the compiled spec,
// activation response and other run state stay in memory only (activation keys must never be persisted here).
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { ActivateResponse, AppSpec, BBox, CompileInput, DestinationPoint } from "../api/types";

export type AppStatus = "idle" | "compiling" | "proposed" | "activating" | "active";

interface BuilderState {
  /** Human display name ("Harbour watch"); the app id is derived from it unless edited. */
  name: string;
  appId: string;
  /** True once the dev edited the app id by hand, so renaming stops re-deriving it. */
  appIdCustom: boolean;
  intent: string;
  bbox: BBox | null;
  cellM: number;
  destinationPoints: DestinationPoint[];
  sources: CompileInput[];
  /** Sample webhook payloads the dev pasted, by source id (only for the client-side preview; never sent). */
  webhookSamples: Record<string, string>;
  /** ISO time of the last successful compile of this draft (null while it's still being built). */
  compiledAt: string | null;

  compiledSpec: AppSpec | null;
  compiling: boolean;
  compileError: string;

  activation: ActivateResponse | null;
  appStatus: AppStatus;

  selectedLayerId: string | null;

  setName: (name: string) => void;
  setAppId: (id: string) => void;
  setAppIdCustom: (custom: boolean) => void;
  setIntent: (text: string) => void;
  setBbox: (b: BBox) => void;
  setCellM: (m: number) => void;
  addDestinationPoint: (p: DestinationPoint) => void;
  updateDestinationPoint: (index: number, patch: Partial<DestinationPoint>) => void;
  removeDestinationPoint: (index: number) => void;
  addSource: (input: CompileInput) => void;
  removeSource: (id: string) => void;
  setCompiledSpec: (spec: AppSpec | null) => void;
  setCompiledAt: (t: string | null) => void;
  setCompiling: (busy: boolean) => void;
  setCompileError: (msg: string) => void;
  setActivation: (a: ActivateResponse | null) => void;
  setAppStatus: (s: AppStatus) => void;
  setSelectedLayerId: (id: string | null) => void;
  /** Back to an empty draft (and no compiled spec / activation): "Start new". */
  resetDraft: () => void;

  updateSource: (index: number, patch: Partial<CompileInput>) => void;
  setWebhookSample: (sourceId: string, text: string) => void;
  removeSourceAt: (index: number) => void;
  /** Put a source back at its old position (undo of a removal). */
  insertSourceAt: (index: number, input: CompileInput) => void;
}

const initialDraft = {
  name: "",
  appId: "",
  appIdCustom: false,
  intent: "",
  bbox: null as BBox | null,
  cellM: 25,
  destinationPoints: [] as DestinationPoint[],
  sources: [] as CompileInput[],
  webhookSamples: {} as Record<string, string>,
  compiledAt: null as string | null,
  compiledSpec: null as AppSpec | null,
  compiling: false,
  compileError: "",
  activation: null as ActivateResponse | null,
  appStatus: "idle" as AppStatus,
  selectedLayerId: null as string | null,
};

/** Keeps a pasted sample from filling sessionStorage. */
export const MAX_SAMPLE_CHARS = 20_000;

/** sessionStorage can throw (private mode, blocked storage): fall back to a no-op store. */
const safeSession = createJSONStorage(() => {
  try {
    const s = window.sessionStorage;
    s.getItem("__probe");
    return s;
  } catch {
    return { getItem: () => null, setItem: () => undefined, removeItem: () => undefined };
  }
});

export const useBuilderStore = create<BuilderState>()(
  persist(
    (set) => ({
      ...initialDraft,

      setName: (name) => set({ name }),
      setAppId: (id) => set({ appId: id }),
      setAppIdCustom: (custom) => set({ appIdCustom: custom }),
      setIntent: (text) => set({ intent: text }),
      setBbox: (b) => set({ bbox: b }),
      setCellM: (m) => set({ cellM: m }),
      addDestinationPoint: (p) => set((s) => ({ destinationPoints: [...s.destinationPoints, p] })),
      updateDestinationPoint: (index, patch) =>
        set((s) => ({ destinationPoints: s.destinationPoints.map((p, i) => (i === index ? { ...p, ...patch } : p)) })),
      removeDestinationPoint: (index) =>
        set((s) => ({ destinationPoints: s.destinationPoints.filter((_, i) => i !== index) })),
      addSource: (input) => set((s) => ({ sources: [...s.sources.filter((x) => x.id !== input.id), input] })),
      removeSource: (id) => set((s) => ({ sources: s.sources.filter((x) => x.id !== id) })),
      setCompiledSpec: (spec) => set({ compiledSpec: spec }),
      setCompiledAt: (t) => set({ compiledAt: t }),
      setCompiling: (busy) => set({ compiling: busy }),
      setCompileError: (msg) => set({ compileError: msg }),
      setActivation: (a) => set({ activation: a }),
      setAppStatus: (status) => set({ appStatus: status }),
      setSelectedLayerId: (id) => set({ selectedLayerId: id }),
      resetDraft: () => set({ ...initialDraft }),

      updateSource: (index, patch) =>
        set((s) => {
          const old = s.sources[index]?.id;
          const renamed = old !== undefined && patch.id !== undefined && patch.id !== old && old in s.webhookSamples;
          const webhookSamples = renamed ? { ...s.webhookSamples, [patch.id as string]: s.webhookSamples[old] ?? "" } : s.webhookSamples;
          if (renamed) delete webhookSamples[old];
          return { sources: s.sources.map((x, i) => (i === index ? { ...x, ...patch } : x)), webhookSamples };
        }),
      setWebhookSample: (sourceId, text) =>
        set((s) => ({ webhookSamples: { ...s.webhookSamples, [sourceId]: text.slice(0, MAX_SAMPLE_CHARS) } })),
      removeSourceAt: (index) => set((s) => ({ sources: s.sources.filter((_, i) => i !== index) })),
      insertSourceAt: (index, input) =>
        set((s) => {
          const next = s.sources.filter((x) => x.id !== input.id);
          next.splice(Math.min(Math.max(index, 0), next.length), 0, input);
          return { sources: next };
        }),
    }),
    {
      name: "builder-draft",
      version: 1,
      storage: safeSession,
      // Draft fields only. Never the compiled spec, the activation (it carries keys) or run state.
      partialize: (s) => ({
        name: s.name,
        appId: s.appId,
        appIdCustom: s.appIdCustom,
        intent: s.intent,
        bbox: s.bbox,
        cellM: s.cellM,
        destinationPoints: s.destinationPoints,
        sources: s.sources,
        webhookSamples: s.webhookSamples,
        compiledAt: s.compiledAt,
      }),
    },
  ),
);
