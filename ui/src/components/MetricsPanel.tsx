import { useEffect, useState } from "react";
import { api } from "../api";
import type { MetricsReport } from "../types";

function pct(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(0)}%`;
}

function secs(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}s`;
}

export default function MetricsPanel({ runId }: { runId: string }) {
  const [report, setReport] = useState<MetricsReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.metrics(runId).then(setReport).catch((e) => setError(String(e)));
  }, [runId]);

  if (error) return <div className="muted">{error}</div>;
  if (!report) return <div className="muted">loading…</div>;

  const durations = Object.entries(report.stage_durations).sort((a, b) => b[1] - a[1]);
  const max = durations.length ? durations[0][1] : 1;

  return (
    <>
      <div className="panel">
        <h2>reliability</h2>
        <dl className="kv">
          <dt>outcome</dt><dd>{report.outcome ?? "—"}</dd>
          <dt>end-to-end</dt><dd>{secs(report.end_to_end_s)}</dd>
          <dt>verification success</dt><dd>{pct(report.success_rate)}</dd>
          <dt>first-attempt success</dt><dd>{pct(report.first_attempt_success_rate)}</dd>
          <dt>retries</dt><dd>{report.retries}</dd>
          <dt>rollbacks</dt><dd>{report.rollbacks}</dd>
          <dt>MTTR</dt><dd>{secs(report.mttr_s)}</dd>
          <dt>unresolved failures</dt><dd>{report.unresolved_failures}</dd>
          <dt>agent cost</dt><dd>${report.cost_usd.toFixed(2)}</dd>
        </dl>
      </div>
      <div className="panel">
        <h2>per-stage time</h2>
        {durations.map(([stage, seconds]) => (
          <div key={stage} className="bar-row">
            <div className="bar-label">{stage}</div>
            <div className="bar" style={{ width: `${(seconds / max) * 60}%` }} />
            <div className="bar-value">{seconds.toFixed(1)}s</div>
          </div>
        ))}
        {durations.length === 0 && <div className="muted">no stage timings yet</div>}
      </div>
    </>
  );
}
