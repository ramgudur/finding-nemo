// Step 2: where the app works. The map is open from the start; destinations and cell size are secondary.
import { useNavigate } from "react-router-dom";
import { StepIntro } from "../../components/builder/Help";
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
      <StepIntro
        step={2}
        title="Where does it work?"
        lead="Frame the area your app covers on the map. Everything it scores, and every route it gives, stays inside this box."
      />

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
