import { useEffect, useState } from "react";
import { api } from "./api";
import type { Health } from "./types";
import Dashboard from "./pages/Dashboard";
import RunDetail from "./pages/RunDetail";

/** Hash routing: "#/" is the dashboard, "#/runs/<id>" a run. */
function useRoute(): string {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export default function App() {
  const route = useRoute();
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, [route]);

  const runMatch = route.match(/^#\/runs\/([a-f0-9]+)/);

  return (
    <>
      <div className="topbar">
        <a className="brand" href="#/">
          software <span>factory</span>
        </a>
        <div className="spacer" />
        {health && (
          <div className="env">
            <span className={`dot ${health.langfuse_enabled ? "on" : "off"}`} />
            langfuse {health.langfuse_enabled ? "tracing" : "off"}
            {" · "}impl: {health.implementer_model}
            {" · "}acceptance: {health.acceptance_dir.includes("no-acceptance") ? "skipped" : "on"}
          </div>
        )}
      </div>
      <div className="page">
        {runMatch ? <RunDetail runId={runMatch[1]} /> : <Dashboard />}
      </div>
    </>
  );
}
