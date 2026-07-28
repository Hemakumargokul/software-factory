/** Generic renderer for spec/design/risk objects: nested objects become
 * definition lists, arrays become bullet lists — readable without
 * hardcoding every stage's JSON schema. */
export default function Structured({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="muted">—</span>;
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return <span>{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="muted">(none)</span>;
    return (
      <ul className="list-block">
        {value.map((item, i) => (
          <li key={i}>
            <Structured value={item} />
          </li>
        ))}
      </ul>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>);
  return (
    <dl className="kv">
      {entries.map(([key, val]) => (
        <div key={key} style={{ display: "contents" }}>
          <dt>{key.replace(/_/g, " ")}</dt>
          <dd>
            <Structured value={val} />
          </dd>
        </div>
      ))}
    </dl>
  );
}
