# Factory web UI

Single-page dashboard for the factory: launch runs, watch them live, approve
or revise at every human gate, and browse what each run produced — spec,
design, risks, code diff, file tree, commit log, metrics, and a per-run
Langfuse trace link.

The backend is the FastAPI app in `src/factory/web/` (run registry, SSE event
stream, gate resume, artifact endpoints with archive-branch fallback for
finished runs). This folder is only the frontend.

## Stack

- Vite + React 18 + TypeScript, no router or state library — a hash-based
  two-page app (`Dashboard`, `RunDetail`).
- Runtime dependencies: `react`, `react-dom`, `marked` (renders markdown in
  gate payloads). Everything else is dev tooling.
- Live updates arrive over Server-Sent Events from
  `/api/runs/{run_id}/events`; no polling.

## Build (required before `factory ui` can serve the SPA)

The compiled bundle is **not** committed — `src/factory/web/static/` is
gitignored, so on a fresh clone you must build once:

```bash
cd ui
npm install
npm run build     # tsc -b && vite build → ../src/factory/web/static/
```

Then serve everything from one process, no node runtime needed:

```bash
pip install -e '.[ui]'
factory ui        # http://127.0.0.1:8500
```

`factory ui` applies the Langfuse dev keys by default, so every run started
from the UI is traced without extra setup.

## Development

```bash
cd ui
npm run dev       # Vite dev server with HMR
```

The dev server proxies `/api` to `http://127.0.0.1:8500` (see
`vite.config.ts`), so run `factory ui` in another terminal to have a live
backend. Rebuild with `npm run build` when done — the served bundle only
updates on build.

## Layout

```
src/
  main.tsx               entry point
  App.tsx                hash routing + backend health badge
  api.ts                 fetch wrappers + SSE subscription
  types.ts               API response types
  styles.css             dark theme, all component styles
  pages/
    Dashboard.tsx        run list + new-run form
    RunDetail.tsx        pipeline view, live events, gate card, artifact tabs
  components/
    Pipeline.tsx         stage progress strip
    GateCard.tsx         HITL approve / revise / reject
    Structured.tsx       recursive renderer for spec/design/risks JSON
    DiffView.tsx         unified diff with add/del/hunk highlighting
    FileBrowser.tsx      collapsible folder tree + file viewer
    MetricsPanel.tsx     reliability metrics + stage durations
```
