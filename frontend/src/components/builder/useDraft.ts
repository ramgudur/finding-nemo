// The builder draft as one object, plus the app ids this browser knows are live (so About can refuse them).
import { useMemo } from "react";
import { useShallow } from "zustand/react/shallow";
import { activeProjectIds } from "../../api/authorityKey";
import { useBuilderStore } from "../../store/useBuilderStore";
import type { SourcesDraft } from "../sources/sourceUtils";

export function useDraft(): SourcesDraft {
  return useBuilderStore(
    useShallow((s) => ({
      name: s.name,
      appId: s.appId,
      intent: s.intent,
      bbox: s.bbox,
      cellM: s.cellM,
      destinationPoints: s.destinationPoints,
      sources: s.sources,
    })),
  );
}

export function useActiveIds(): string[] {
  const activated = useBuilderStore((s) => (s.appStatus === "active" ? s.activation?.app_id ?? s.appId : null));
  return useMemo(() => {
    const ids = activeProjectIds();
    return activated && !ids.includes(activated) ? [...ids, activated] : ids;
  }, [activated]);
}
