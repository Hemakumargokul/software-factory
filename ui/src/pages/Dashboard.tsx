import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import type { RunRow } from "../types";

function statusOf(run: RunRow): { label: string; cls: string } {
  if (run.killed) return { label: "killed", cls: "killed" };
  if (run.driving) return { label: "running", cls: "running" };
  if (run.outcome === "finished") return { label: "finished", cls: "finished" };
  if (run.outcome === "safe_stopped") return { label: "safe-stopped", cls: "safe_stopped" };
  if (run.outcome === "paused") return { label: "waiting on you", cls: "waiting" };
  return { label: run.outcome ?? "unknown", cls: "stopped" };
}

export default function Dashboard() {
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [error, setError] = useState("");
  const [goal, setGoal] = useState("");
  const [budget, setBudget] = useState("5");
  const [model, setModel] = useState("");
  const [acceptance, setAcceptance] = useState("skip");
  const [auto, setAuto] = useState(false);
  const [starting, setStarting] = useState(false);

  const refresh = () => api.runs().then(setRuns).catch((e) => setError(String(e)));

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, []);

  async function start(event: FormEvent) {
    event.preventDefault();
    setStarting(true);
    setError("");
    try {
      const body: Parameters<typeof api.start>[0] = {
        goal,
        acceptance,
        auto,
        budget: parseFloat(budget) || undefined,
      };
      if (model) body.implementer_model = model;
      const { run_id } = await api.start(body);
      window.location.hash = `#/runs/${run_id}`;
    } catch (e) {
      setError(String(e));
    } finally {
      setStarting(false);
    }
  }

  return (
    <>
      {error && <div className="error-banner">{error}</div>}

      <div className="panel">
        <h2>New run</h2>
        <form onSubmit={start}>
          <div className="field">
            <label>Goal — the engineering request, contract details included</label>
            <textarea
              rows={5}
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="Build a ... web service with EXACTLY this HTTP contract: ..."
              required
            />
          </div>
          <div className="row">
            <div className="field">
              <label>Budget (USD)</label>
              <input value={budget} onChange={(e) => setBudget(e.target.value)} />
            </div>
            <div className="field">
              <label>Implementer model</label>
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">haiku (default, cheapest)</option>
                <option value="sonnet">sonnet (stronger)</option>
                <option value="opus">opus (strongest)</option>
              </select>
            </div>
            <div className="field">
              <label>Acceptance suite</label>
              <select value={acceptance} onChange={(e) => setAcceptance(e.target.value)}>
                <option value="skip">skip (rely on unit tests + review)</option>
                <option value="default">tests/acceptance (URL shortener contract)</option>
              </select>
            </div>
            <div className="field">
              <label>Gates</label>
              <select
                value={auto ? "auto" : "hitl"}
                onChange={(e) => setAuto(e.target.value === "auto")}
              >
                <option value="hitl">human approval (HITL)</option>
                <option value="auto">auto-approve (unattended)</option>
              </select>
            </div>
          </div>
          <button className="primary" disabled={starting || !goal.trim()}>
            {starting ? "starting..." : "start run"}
          </button>
        </form>
      </div>

      <div className="panel">
        <h2>Runs</h2>
        {runs.length === 0 ? (
          <div className="muted">No runs yet — start one above.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>run</th>
                <th>status</th>
                <th>started</th>
                <th>scenario</th>
                <th>goal</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const status = statusOf(run);
                return (
                  <tr
                    key={run.run_id}
                    className="clickable"
                    onClick={() => (window.location.hash = `#/runs/${run.run_id}`)}
                  >
                    <td className="mono">{run.run_id}</td>
                    <td>
                      <span className={`chip ${status.cls}`}>{status.label}</span>
                    </td>
                    <td className="muted">
                      {run.started ? new Date(run.started).toLocaleString() : "—"}
                    </td>
                    <td className="muted">{run.scenario ?? "—"}</td>
                    <td className="muted">
                      {(run.goal ?? "").slice(0, 90)}
                      {(run.goal ?? "").length > 90 ? "…" : ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
