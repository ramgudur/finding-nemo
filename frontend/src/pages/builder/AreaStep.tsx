// Step 2: where the app works. The map is open from the start; destinations and cell size are secondary.
import { useNavigate } from "react-router-dom";
import { StepFooter } from "../../components/builder/StepFooter";
import { useDraft } from "../../components/builder/useDraft";
import { AreaPicker } from "../../components/sources/AreaPicker";
import { areaProblems } from "../../components/sources/sourceUtils";
import { useBuilderStore } from "../../store/useBuilderStore";

export function AreaStep() {
  const navigate = useNavigate();
  const draft = useDraft();
  const s = useBuilderStore.getState();
  const problems = areaProblems(draft);

  return (
    <>
      <header className="flex flex-col gap-1.5">
        <h1 className="text-[28px] font-semibold leading-tight tracking-[-0.02em] text-slate">Where does it work?</h1>
        <p className="max-w-[62ch] text-[15px] text-ink">
          Frame the area on the map. Everything the app scores, and every route it gives, stays inside it.
        </p>
      </header>

      <AreaPicker
        bbox={draft.bbox}
        onBbox={s.setBbox}
        cellM={draft.cellM}
        onCellM={s.setCellM}
        points={draft.destinationPoints}
        onAddPoint={s.addDestinationPoint}
        onUpdatePoint={s.updateDestinationPoint}
        onRemovePoint={s.removeDestinationPoint}
      />

      <StepFooter
        back={{ to: "/new/about", label: "About" }}
        problems={problems}
        readyText="Next, add the data the app listens to."
        next={{ label: "Next: Sources", onClick: () => problems.length === 0 && navigate("/new/sources") }}
      />
    </>
  );
}
