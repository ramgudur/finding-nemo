interface Props {
  layerIds: string[];
  visible: string[];
  onChange: (visible: string[]) => void;
}

/** One checkbox per layer the API returned; "score" (the combined map) is listed first. */
export function LayerToggles({ layerIds, visible, onChange }: Props) {
  const ordered = [...layerIds].sort((a, b) => (a === "score" ? -1 : b === "score" ? 1 : a.localeCompare(b)));
  const toggle = (id: string) =>
    onChange(visible.includes(id) ? visible.filter((v) => v !== id) : [...visible, id]);

  if (ordered.length === 0) return <p style={{ color: "#666" }}>No layers yet.</p>;
  return (
    <fieldset style={{ border: "1px solid #ddd", borderRadius: 4, padding: "8px 12px" }}>
      <legend>Layers</legend>
      {ordered.map((id) => (
        <label key={id} style={{ display: "block", padding: "2px 0", cursor: "pointer" }}>
          <input type="checkbox" checked={visible.includes(id)} onChange={() => toggle(id)} />{" "}
          {id === "score" ? "score (combined)" : id}
        </label>
      ))}
    </fieldset>
  );
}
