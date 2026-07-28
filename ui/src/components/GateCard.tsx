import { useState } from "react";
import type { GatePayload } from "../types";
import Structured from "./Structured";
import DiffView from "./DiffView";

const HIDDEN_KEYS = new Set(["gate", "question", "diff"]);

export default function GateCard({
  payload,
  onAnswer,
  busy,
}: {
  payload: GatePayload;
  onAnswer: (action: string, edits: string) => void;
  busy: boolean;
}) {
  const [edits, setEdits] = useState("");
  const [showRevise, setShowRevise] = useState(false);
  const isClarify = payload.gate === "clarify";
  const isMerge = payload.gate === "merge";

  const details = Object.entries(payload).filter(
    ([key, value]) =>
      !HIDDEN_KEYS.has(key) &&
      value !== null &&
      value !== "" &&
      !(Array.isArray(value) && value.length === 0),
  );

  return (
    <div className="gate-card">
      <h2>human gate: {payload.gate}</h2>
      <div style={{ fontWeight: 600, marginBottom: 10 }}>{payload.question}</div>

      {details.map(([key, value]) => (
        <div key={key} className="field">
          <label>{key.replace(/_/g, " ")}</label>
          <Structured value={value} />
        </div>
      ))}

      {typeof payload.diff === "string" && payload.diff && (
        <div className="field">
          <label>diff</label>
          <DiffView diff={payload.diff} />
        </div>
      )}

      <div className="gate-actions">
        <button className="approve" disabled={busy} onClick={() => onAnswer("approve", "")}>
          approve
        </button>
        {!isMerge && (
          <button disabled={busy} onClick={() => setShowRevise(!showRevise)}>
            {isClarify ? "answer questions" : "request changes"}
          </button>
        )}
        <button className="danger" disabled={busy} onClick={() => onAnswer("reject", "")}>
          reject
        </button>
      </div>

      {showRevise && (
        <div style={{ marginTop: 12 }}>
          <textarea
            rows={4}
            value={edits}
            onChange={(e) => setEdits(e.target.value)}
            placeholder={
              isClarify
                ? "Answers to the clarification questions..."
                : "What should change? The stage re-runs with these edits and downstream work is invalidated."
            }
          />
          <button
            className="primary"
            style={{ marginTop: 8 }}
            disabled={busy || !edits.trim()}
            onClick={() => onAnswer("revise", edits)}
          >
            send {isClarify ? "answers" : "revision"}
          </button>
        </div>
      )}
    </div>
  );
}
