// Step 3: the data the app listens to. The big input only ever describes ONE source; the real
// /sources/suggest answers with ways to connect it (sent by the dev's app, or fetched by the platform).
// Picking one adds it to the list below, where its id and extra fields can be edited.
import { AnimatePresence } from "motion/react";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { CompileInput, SourceSuggestion } from "../../api/types";
import { api } from "../../api/client";
import { BigInput } from "../../components/builder/BigInput";
import { FailurePanel, toFailure } from "../../components/builder/Failure";
import type { Failure } from "../../components/builder/Failure";
import { StepIntro } from "../../components/builder/Help";
import { SourceRow } from "../../components/builder/SourceRow";
import { StepFooter } from "../../components/builder/StepFooter";
import { useUndoToast } from "../../components/builder/UndoToast";
import { useDraft } from "../../components/builder/useDraft";
import { SourceSuggestions } from "../../components/sources/SourceSuggestions";
import { sourcesProblems } from "../../components/sources/sourceUtils";
import { useBuilderStore } from "../../store/useBuilderStore";

/** Stable React keys for sources (ids are editable, so they can't be the key). */
function rowKeys(sources: CompileInput[]): string[] {
  return sources.map((s, i) => `${s.meaning}#${sources.slice(0, i).filter((x) => x.meaning === s.meaning).length}`);
}

export function SourcesStep() {
  const navigate = useNavigate();
  const draft = useDraft();
  const s = useBuilderStore.getState();
  const { show, node: toast } = useUndoToast();

  const [text, setText] = useState("");
  const [suggestion, setSuggestion] = useState<SourceSuggestion | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [fieldProblems, setFieldProblems] = useState<Record<string, string>>({});
  const seq = useRef(0);

  const keys = rowKeys(draft.sources);
  const fieldProblem = keys.map((k) => fieldProblems[k]).find(Boolean);
  const problems = [...sourcesProblems(draft), ...(fieldProblem ? [`Extra fields: ${fieldProblem}`] : [])];
  const ids = draft.sources.map((x) => x.id);

  const suggest = async () => {
    const v = text.trim();
    if (!v) return;
    const mine = ++seq.current;
    setSuggesting(true);
    setFailure(null);
    try {
      const sug = await api.suggestSource(draft.appId, { intent: draft.intent.trim(), description: v });
      if (mine === seq.current) setSuggestion(sug);
    } catch (e) {
      if (mine === seq.current) setFailure(toFailure(e, "suggest", draft.appId));
    } finally {
      if (mine === seq.current) setSuggesting(false);
    }
  };

  const add = (input: CompileInput) => {
    s.addSource(input);
    setSuggestion(null);
    setText("");
  };

  const remove = (i: number) => {
    const removed = draft.sources[i];
    const key = keys[i] ?? "";
    if (!removed) return;
    s.removeSourceAt(i);
    setFieldProblems((p) => {
      const next = { ...p };
      delete next[key];
      return next;
    });
    show(`Removed ${removed.id}`, () => useBuilderStore.getState().insertSourceAt(i, removed));
  };

  const count = draft.sources.length;

  return (
    <>
      <StepIntro
        step={3}
        title="Data sources"
        lead="Add the data your app listens to, one source at a time. Say in plain words what it is and what one item means; we suggest ways to connect it."
      />

      <BigInput
        id="source-description"
        label="Describe a data source"
        placeholder="e.g. people tap a button in our app when they see a problem"
        value={text}
        onChange={setText}
        onSubmit={() => void suggest()}
        submitHint="to find ways to connect it"
        busy={suggesting}
        busyText="looking for ways to connect it…"
        autoFocus={count === 0}
      />

      <AnimatePresence mode="wait">
        {suggestion && (
          <SourceSuggestions
            key={suggestion.meaning}
            suggestion={suggestion}
            bbox={draft.bbox}
            takenIds={ids}
            onAdd={add}
            onDismiss={() => setSuggestion(null)}
          />
        )}
      </AnimatePresence>

      {failure && <FailurePanel failure={failure} onClose={() => setFailure(null)} />}

      <section aria-labelledby="added-h" className="flex flex-col gap-3">
        <h2 id="added-h" className="bld-h2">
          Your sources {count > 0 && <span className="font-normal text-ink">({count})</span>}
        </h2>
        {count === 0 ? (
          <p className="bld-card-quiet px-4 py-4 text-sm text-ink">
            Nothing yet. Describe a source above, then pick how it connects. Each source becomes one or more layers on
            your map.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            <AnimatePresence initial={false} mode="popLayout">
              {draft.sources.map((src, i) => {
                const key = keys[i] ?? String(i);
                return (
                <SourceRow
                  key={key}
                  source={src}
                  duplicate={ids.indexOf(src.id) !== i}
                  onRename={(id) => s.updateSource(i, { id })}
                  onFields={(fields) => s.updateSource(i, { fields })}
                  onWebhook={(webhook) => s.updateSource(i, { webhook })}
                  onConnector={(connector) => s.updateSource(i, { connector })}
                  onFieldsProblem={(p) =>
                    setFieldProblems((cur) => {
                      const next = { ...cur };
                      if (p) next[key] = p;
                      else delete next[key];
                      return next;
                    })
                  }
                  onRemove={() => remove(i)}
                />
                );
              })}
            </AnimatePresence>
          </ul>
        )}
      </section>

      {toast}

      <StepFooter
        back={{ to: "/new/area", label: "Area" }}
        summary={count > 0 ? `${count} source${count === 1 ? "" : "s"} added` : undefined}
        problems={problems}
        readyText="Next, review everything and compile."
        next={{ label: "Next: Review", onClick: () => problems.length === 0 && navigate("/new/review") }}
      />
    </>
  );
}
