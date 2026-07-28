function classOf(line: string): string {
  if (line.startsWith("diff --git")) return "file";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+++") || line.startsWith("---")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "";
}

export default function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) {
    return <div className="muted">No changes against the base commit.</div>;
  }
  return (
    <div className="diff-wrap">
      {diff.split("\n").map((line, i) => (
        <div key={i} className={`diff-line ${classOf(line)}`}>
          {line || " "}
        </div>
      ))}
    </div>
  );
}
