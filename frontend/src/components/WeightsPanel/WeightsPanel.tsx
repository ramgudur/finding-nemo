// Current layer weights with tier and pin state; the dev can pin/unpin (recorded as a Change).
import type { WeightsView } from "../../api/types";

interface Props {
  view: WeightsView;
  busy: boolean;
  onPin: (layerId: string, pinned: boolean) => void;
}

export function WeightsPanel({ view, busy, onPin }: Props) {
  return (
    <fieldset style={{ border: "1px solid #ddd", borderRadius: 4, padding: "8px 12px" }}>
      <legend>Weights</legend>
      <table style={{ borderCollapse: "collapse", fontSize: 14 }}>
        <thead>
          <tr style={{ color: "#666", textAlign: "left" }}>
            <th style={{ padding: "2px 8px 2px 0" }}>layer</th>
            <th style={{ padding: "2px 8px" }}>tier</th>
            <th style={{ padding: "2px 8px", textAlign: "right" }}>weight</th>
            <th style={{ padding: "2px 0 2px 8px" }}>pin</th>
          </tr>
        </thead>
        <tbody>
          {view.layers.map((l) => (
            <tr key={l.layer_id}>
              <td style={{ padding: "2px 8px 2px 0" }}>{l.layer_id}</td>
              <td style={{ padding: "2px 8px", color: "#444" }}>{l.tier}</td>
              <td style={{ padding: "2px 8px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{l.weight.toFixed(3)}</td>
              <td style={{ padding: "2px 0 2px 8px" }}>
                <label>
                  <input
                    type="checkbox"
                    checked={l.pinned}
                    disabled={busy}
                    onChange={(e) => onPin(l.layer_id, e.target.checked)}
                    aria-label={`pin ${l.layer_id}`}
                  />{" "}
                  {l.pinned ? "pinned" : ""}
                </label>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p style={{ margin: "6px 0 0", color: "#444", fontSize: 14 }}>
        Total flexible weight: <strong style={{ fontVariantNumeric: "tabular-nums" }}>{view.flexible_total.toFixed(3)}</strong>{" "}
        <span style={{ color: "#666" }}>(stays fixed when flexible weights are renormalized)</span>
      </p>
    </fieldset>
  );
}
