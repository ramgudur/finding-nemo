// Shared in-memory draft state across Sources <-> Architecture navigation. Not persisted automatically
// (persistence is the explicit "Save Project" action in api/authorityKey.ts's recordProject) so that
// navigating Home -> Sources resumes an in-progress draft rather than silently writing to storage.
import { create } from "zustand";
import type { ActivateResponse, AppSpec, BBox, CompileInput, DestinationPoint } from "../api/types";

export type AppStatus = "idle" | "compiling" | "proposed" | "activating" | "active";

interface BuilderState {
  appId: string;
  intent: string;
  bbox: BBox | null;
  cellM: number;
  destinationPoints: DestinationPoint[];
  sources: CompileInput[];

  compiledSpec: AppSpec | null;
  compiling: boolean;
  compileError: string;

  activation: ActivateResponse | null;
  appStatus: AppStatus;

  selectedLayerId: string | null;

  setAppId: (id: string) => void;
  setIntent: (text: string) => void;
  setBbox: (b: BBox) => void;
  setCellM: (m: number) => void;
  addDestinationPoint: (p: DestinationPoint) => void;
  updateDestinationPoint: (index: number, patch: Partial<DestinationPoint>) => void;
  removeDestinationPoint: (index: number) => void;
  addSource: (input: CompileInput) => void;
  removeSource: (id: string) => void;
  setCompiledSpec: (spec: AppSpec | null) => void;
  setCompiling: (busy: boolean) => void;
  setCompileError: (msg: string) => void;
  setActivation: (a: ActivateResponse | null) => void;
  setAppStatus: (s: AppStatus) => void;
  setSelectedLayerId: (id: string | null) => void;
  resetDraft: () => void;
}

const initialDraft = {
  appId: "",
  intent: "",
  bbox: null as BBox | null,
  cellM: 25,
  destinationPoints: [] as DestinationPoint[],
  sources: [] as CompileInput[],
  compiledSpec: null as AppSpec | null,
  compiling: false,
  compileError: "",
  activation: null as ActivateResponse | null,
  appStatus: "idle" as AppStatus,
  selectedLayerId: null as string | null,
};

export const useBuilderStore = create<BuilderState>((set) => ({
  ...initialDraft,

  setAppId: (id) => set({ appId: id }),
  setIntent: (text) => set({ intent: text }),
  setBbox: (b) => set({ bbox: b }),
  setCellM: (m) => set({ cellM: m }),
  addDestinationPoint: (p) => set((s) => ({ destinationPoints: [...s.destinationPoints, p] })),
  updateDestinationPoint: (index, patch) =>
    set((s) => ({ destinationPoints: s.destinationPoints.map((p, i) => (i === index ? { ...p, ...patch } : p)) })),
  removeDestinationPoint: (index) => set((s) => ({ destinationPoints: s.destinationPoints.filter((_, i) => i !== index) })),
  addSource: (input) => set((s) => ({ sources: [...s.sources.filter((x) => x.id !== input.id), input] })),
  removeSource: (id) => set((s) => ({ sources: s.sources.filter((x) => x.id !== id) })),
  setCompiledSpec: (spec) => set({ compiledSpec: spec }),
  setCompiling: (busy) => set({ compiling: busy }),
  setCompileError: (msg) => set({ compileError: msg }),
  setActivation: (a) => set({ activation: a }),
  setAppStatus: (status) => set({ appStatus: status }),
  setSelectedLayerId: (id) => set({ selectedLayerId: id }),
  resetDraft: () => set({ ...initialDraft }),
}));
