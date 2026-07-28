import { useCallback, useEffect, useRef, useState } from "react";
import { marked } from "marked";
import { api, subscribe } from "../api";
import type { RunDetail as Detail, RunEvent } from "../types";
import GateCard from "../components/GateCard";
import Pipeline from "../components/Pipeline";
import Structured from "../components/Structured";
import DiffView from "../components/DiffView";
import FileBrowser from "../components/FileBrowser";
import MetricsPanel from "../components/MetricsPanel";

const TABS = ["overview", "spec", "design", "diff", "files", "metrics", "summary"] as const;
type Tab = (typeof TABS)[number];

function statusChip(detail: Detail): { label: string; cls: string } {
  if (detail.killed) return { label: "killed", cls: "killed" };
  if (detail.driving) return { label: "running", cls: "running" };
  if (detail.pending_gate) return { label: "waiting on you", cls: "waiting" };
  if (detail.safe_stop) return { label: "safe-stopped", cls: "safe_stopped" };
  if (detail.stages_done.includes("summary")) return { label: "finished", cls: "finished" };
  return { label: "parked", cls: "paused" };
}

export default function RunDetail({ runId }: { runId: string }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [tab, setTab] = useState<Tab>("overview");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [diff, setDiff] = useState<string | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  const feedRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(
    () => api.run(runId).then(setDetail).catch((e) => setError(String(e))),
    [runId],
  );

  useEffect(() => {
    refresh();
    const unsubscribe = subscribe(runId, (event) => {
      setEvents((prev) => [...prev.filter((e) => e.seq !== event.seq), event]);
      if (["gate", "done", "killed", "error", "node"].includes(event.type)) refresh();
    });
    const timer = setInterval(refresh, 5000);
    return () => {
      unsubscribe();
      clearInterval(timer);
    };
  }, [runId, refresh]);

  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight });
  }, [events]);

  useEffect(() => {
    if (tab === "diff") {
      api.diff(runId).then((d) => setDiff(d.diff)).catch((e) => setDiff(`unavailable: ${e}`));
    }
    if (tab === "summary") {
      api.summary(runId)
        .then((s) => setSummary(s.markdown))
        .catch((e) => setSummary(`_${e}_`));
    }
  }, [tab, runId, detail?.driving]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!detail) {
    return error ? <div className="error-banner">{error}</div> : <div className="muted">loading…</div>;
  }

  const status = statusChip(detail);

  return (
    <>
      {error && <div className="error-banner">{error}</div>}

      <div className="panel">
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span className="mono" style={{ fontSize: 18, fontWeight: 700 }}>{runId}</span>
          <span className={`chip ${status.cls}`}>{status.label}</span>
          <span className="muted">
            ${detail.spend_usd.toFixed(2)}
            {detail.budget_usd ? ` of $${detail.budget_usd.toFixed(2)}` : ""}
          </span>
          <span className="muted">
            task {Math.min(detail.task_idx + 1, Math.max(detail.tasks.length, 1))}/{detail.tasks.length || "?"}
            {" · "}attempt {detail.attempts}
          </span>
          <div className="spacer" style={{ flex: 1 }} />
          {detail.langfuse_url && (
            <a href={detail.langfuse_url} target="_blank" rel="noreferrer">
              open in langfuse ↗
            </a>
          )}
          {!detail.killed && (detail.driving || detail.pending_gate) && (
            <button className="danger" disabled={busy} onClick={() => act(() => api.kill(runId))}>
              kill
            </button>
          )}
          {detail.killed && (
            <button
              disabled={busy}
              onClick={() =>
                act(async () => {
                  await api.clearKill(runId);
                  await api.resume(runId);
                })
              }
            >
              clear kill &amp; resume
            </button>
          )}
          {!detail.killed && !detail.driving && !detail.pending_gate &&
            !detail.stages_done.includes("summary") && !detail.safe_stop && (
            <button disabled={busy} onClick={() => act(() => api.resume(runId))}>
              resume
            </button>
          )}
        </div>
        <div className="muted" style={{ marginTop: 8 }}>{detail.goal}</div>
        <div style={{ marginTop: 12 }}>
          <Pipeline done={detail.stages_done} next={detail.next} />
        </div>
        {detail.safe_stop && (
          <div className="error-banner" style={{ marginTop: 12 }}>
            safe-stopped: {detail.safe_stop.reason} — sandbox preserved at{" "}
            <span className="mono">{detail.sandbox}</span>
          </div>
        )}
      </div>

      {detail.pending_gate && !detail.driving && !detail.killed && (
        <GateCard
          payload={detail.pending_gate}
          busy={busy}
          onAnswer={(action, edits) => act(() => api.gate(runId, action, edits))}
        />
      )}

      <div className="tabs">
        {TABS.map((name) => (
          <button
            key={name}
            className={tab === name ? "active" : ""}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <>
          {detail.tasks.length > 0 && (
            <div className="panel">
              <h2>tasks</h2>
              <table>
                <thead>
                  <tr><th>id</th><th>title</th><th>depends on</th><th>status</th></tr>
                </thead>
                <tbody>
                  {detail.tasks.map((task, index) => (
                    <tr key={task.id}>
                      <td className="mono">{task.id}</td>
                      <td>{task.title}</td>
                      <td className="mono muted">{(task.depends_on ?? []).join(", ") || "—"}</td>
                      <td>
                        {index < detail.task_idx ? (
                          <span className="chip finished">integrated</span>
                        ) : index === detail.task_idx && detail.driving ? (
                          <span className="chip running">in progress</span>
                        ) : (
                          <span className="chip paused">pending</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="panel">
            <h2>live events</h2>
            <div className="feed" ref={feedRef}>
              {events.length === 0 && (
                <div className="muted">
                  No live events in this session — history is in the decisions below.
                </div>
              )}
              {events.map((event) => (
                <div key={`${event.seq}-${event.at}`} className="feed-item">
                  <span className="time">{new Date(event.at).toLocaleTimeString()}</span>
                  {event.type === "node" && (
                    <>
                      <span className="node">● {event.node}</span>
                      {(event.decisions ?? []).map((d, i) => (
                        <div key={i} className="decision">{d.decision}</div>
                      ))}
                    </>
                  )}
                  {event.type === "gate" && (
                    <span style={{ color: "var(--yellow)" }}>
                      ⏸ waiting for you: {event.payload?.gate} gate
                    </span>
                  )}
                  {event.type === "auto_approve" && (
                    <span className="muted">auto-approved gate: {event.gate}</span>
                  )}
                  {event.type === "done" && (
                    <span style={{ color: "var(--green)" }}>run {event.outcome}</span>
                  )}
                  {event.type === "killed" && (
                    <span style={{ color: "var(--red)" }}>run killed</span>
                  )}
                  {event.type === "kill_pending" && (
                    <span style={{ color: "var(--red)" }}>kill switch: stopping at stage boundary</span>
                  )}
                  {event.type === "error" && (
                    <span style={{ color: "var(--red)" }}>error: {event.message}</span>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <h2>decision lineage</h2>
            <div className="feed">
              {detail.decisions.map((decision, i) => (
                <div key={i} className="feed-item">
                  <span className="time">{new Date(decision.at).toLocaleTimeString()}</span>
                  <span className="node">{decision.stage}</span>
                  <div className="decision">{decision.decision}</div>
                  <div className="rationale">{decision.rationale}</div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {tab === "spec" && (
        <div className="panel">
          <h2>specification</h2>
          {detail.spec ? <Structured value={detail.spec} /> : (
            <div className="muted">Not written yet — the requirements stage produces it.</div>
          )}
          {detail.ambiguities.length > 0 && (
            <>
              <h2 style={{ marginTop: 20 }}>open ambiguities</h2>
              <Structured value={detail.ambiguities} />
            </>
          )}
        </div>
      )}

      {tab === "design" && (
        <div className="panel">
          <h2>design</h2>
          {detail.design ? <Structured value={detail.design} /> : (
            <div className="muted">Not written yet — the design stage produces it.</div>
          )}
          {detail.risks.length > 0 && (
            <>
              <h2 style={{ marginTop: 20 }}>risk register</h2>
              <Structured value={detail.risks} />
            </>
          )}
        </div>
      )}

      {tab === "diff" && (
        <div className="panel">
          <h2>
            working tree vs base <span className="mono muted">{detail.base_sha?.slice(0, 8)}</span>
          </h2>
          {diff === null ? <div className="muted">loading…</div> : <DiffView diff={diff} />}
        </div>
      )}

      {tab === "files" && <FileBrowser runId={runId} />}

      {tab === "metrics" && <MetricsPanel runId={runId} />}

      {tab === "summary" && (
        <div className="panel markdown">
          {summary === null ? (
            <div className="muted">loading…</div>
          ) : (
            <div dangerouslySetInnerHTML={{ __html: marked.parse(summary) as string }} />
          )}
        </div>
      )}
    </>
  );
}
