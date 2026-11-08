// PLACEHOLDER — replaced by fe-flow-app
import { useParams } from "react-router-dom";

export default function AppKeys() {
  const { appId } = useParams<{ appId: string }>();
  return (
    <main className="mx-auto max-w-3xl px-4 pt-28 text-slate">
      <h1 className="text-2xl font-semibold">AppKeys</h1>
      <p className="font-mono text-sm text-ink">{appId}</p>
    </main>
  );
}
