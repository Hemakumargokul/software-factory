const STAGE_ORDER = [
  "bootstrap", "intake", "clarify", "requirements", "gate_requirements",
  "design", "gate_design", "decompose", "implement", "tests", "policy",
  "review", "sync", "acceptance", "commit", "gate_merge", "integrate",
  "rollback", "release", "summary", "safe_stop",
];

export default function Pipeline({
  done,
  next,
}: {
  done: string[];
  next: string[];
}) {
  const doneSet = new Set(done);
  const nextSet = new Set(next);
  return (
    <div className="pipeline">
      {STAGE_ORDER.map((stage) => {
        // Only show optional stages when they actually happened
        if (
          ["clarify", "rollback", "safe_stop"].includes(stage) &&
          !doneSet.has(stage) &&
          !nextSet.has(stage)
        ) {
          return null;
        }
        const cls = nextSet.has(stage) ? "next" : doneSet.has(stage) ? "done" : "";
        return (
          <span key={stage} className={`stage ${cls}`}>
            {stage}
          </span>
        );
      })}
    </div>
  );
}
