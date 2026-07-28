import type {
  Health,
  MetricsReport,
  RunDetail,
  RunEvent,
  RunRow,
  SandboxFile,
} from "./types";

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return response.json();
}

export const api = {
  health: () => fetch("/api/health").then((r) => json<Health>(r)),
  runs: () => fetch("/api/runs").then((r) => json<RunRow[]>(r)),
  run: (id: string) => fetch(`/api/runs/${id}`).then((r) => json<RunDetail>(r)),
  metrics: (id: string) =>
    fetch(`/api/runs/${id}/metrics`).then((r) => json<MetricsReport>(r)),
  summary: (id: string) =>
    fetch(`/api/runs/${id}/summary`).then((r) => json<{ markdown: string }>(r)),
  diff: (id: string) =>
    fetch(`/api/runs/${id}/diff`).then((r) =>
      json<{ base_sha: string; diff: string }>(r),
    ),
  log: (id: string) =>
    fetch(`/api/runs/${id}/log`).then((r) => json<{ log: string }>(r)),
  files: (id: string) =>
    fetch(`/api/runs/${id}/files`).then((r) => json<SandboxFile[]>(r)),
  file: (id: string, path: string) =>
    fetch(`/api/runs/${id}/file?path=${encodeURIComponent(path)}`).then((r) =>
      json<{ path: string; content: string }>(r),
    ),
  start: (body: {
    goal: string;
    budget?: number;
    implementer_model?: string;
    acceptance: string;
    auto: boolean;
  }) =>
    fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<{ run_id: string }>(r)),
  gate: (id: string, action: string, edits: string) =>
    fetch(`/api/runs/${id}/gate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, edits }),
    }).then((r) => json<{ resumed: boolean }>(r)),
  resume: (id: string) =>
    fetch(`/api/runs/${id}/resume`, { method: "POST" }).then((r) =>
      json<{ resumed: boolean }>(r),
    ),
  kill: (id: string) =>
    fetch(`/api/runs/${id}/kill`, { method: "POST" }).then((r) =>
      json<{ killed: boolean }>(r),
    ),
  clearKill: (id: string) =>
    fetch(`/api/runs/${id}/kill/clear`, { method: "POST" }).then((r) =>
      json<{ killed: boolean }>(r),
    ),
};

/** Subscribe to a run's live events; returns an unsubscribe function. */
export function subscribe(
  runId: string,
  onEvent: (event: RunEvent) => void,
): () => void {
  const source = new EventSource(`/api/runs/${runId}/events`);
  source.onmessage = (message) => {
    const event: RunEvent = JSON.parse(message.data);
    if (event.type !== "heartbeat") onEvent(event);
  };
  // The server closes the stream when the run stops driving; that's fine.
  source.onerror = () => source.close();
  return () => source.close();
}
