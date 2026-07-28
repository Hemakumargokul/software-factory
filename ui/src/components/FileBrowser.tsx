import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { SandboxFile } from "../types";

interface DirNode {
  name: string;
  path: string;
  dirs: DirNode[];
  files: { name: string; path: string; size: number }[];
}

function buildTree(files: SandboxFile[]): DirNode {
  const root: DirNode = { name: "", path: "", dirs: [], files: [] };
  const dirIndex = new Map<string, DirNode>([["", root]]);

  const dirOf = (path: string): DirNode => {
    const existing = dirIndex.get(path);
    if (existing) return existing;
    const cut = path.lastIndexOf("/");
    const parent = dirOf(cut === -1 ? "" : path.slice(0, cut));
    const node: DirNode = {
      name: path.slice(cut + 1),
      path,
      dirs: [],
      files: [],
    };
    parent.dirs.push(node);
    dirIndex.set(path, node);
    return node;
  };

  for (const file of files) {
    const cut = file.path.lastIndexOf("/");
    const dir = dirOf(cut === -1 ? "" : file.path.slice(0, cut));
    dir.files.push({
      name: file.path.slice(cut + 1),
      path: file.path,
      size: file.size,
    });
  }

  // Compress chains of single-child folders: src/main/java -> one row.
  const compress = (node: DirNode): DirNode => {
    while (node.dirs.length === 1 && node.files.length === 0 && node.path !== "") {
      const only = node.dirs[0];
      node = { ...only, name: `${node.name}/${only.name}` };
    }
    return { ...node, dirs: node.dirs.map(compress) };
  };
  return { ...root, dirs: root.dirs.map(compress) };
}

function Folder({
  node,
  depth,
  collapsed,
  toggle,
  selected,
  open,
}: {
  node: DirNode;
  depth: number;
  collapsed: Set<string>;
  toggle: (path: string) => void;
  selected: string;
  open: (path: string) => void;
}) {
  const isCollapsed = collapsed.has(node.path);
  return (
    <>
      {node.path !== "" && (
        <div
          className="tree-dir"
          style={{ paddingLeft: depth * 14 }}
          onClick={() => toggle(node.path)}
        >
          <span className="tree-arrow">{isCollapsed ? "▸" : "▾"}</span> {node.name}
        </div>
      )}
      {!isCollapsed && (
        <>
          {node.dirs.map((dir) => (
            <Folder
              key={dir.path}
              node={dir}
              depth={node.path === "" ? depth : depth + 1}
              collapsed={collapsed}
              toggle={toggle}
              selected={selected}
              open={open}
            />
          ))}
          {node.files.map((file) => (
            <div
              key={file.path}
              className={`tree-file ${selected === file.path ? "active" : ""}`}
              style={{ paddingLeft: (node.path === "" ? depth : depth + 1) * 14 }}
              title={`${file.size} bytes`}
              onClick={() => open(file.path)}
            >
              {file.name}
            </div>
          ))}
        </>
      )}
    </>
  );
}

export default function FileBrowser({ runId }: { runId: string }) {
  const [files, setFiles] = useState<SandboxFile[]>([]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState("");
  const [content, setContent] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.files(runId).then(setFiles).catch((e) => setError(String(e)));
  }, [runId]);

  const tree = useMemo(() => buildTree(files), [files]);

  function toggle(path: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  async function open(path: string) {
    setSelected(path);
    setContent("loading...");
    try {
      const file = await api.file(runId, path);
      setContent(file.content);
    } catch (e) {
      setContent(`could not open: ${e}`);
    }
  }

  if (error) return <div className="error-banner">{error}</div>;

  return (
    <div className="files-layout">
      <div className="file-list panel" style={{ padding: 8 }}>
        {files.length === 0 ? (
          <div className="muted">no files</div>
        ) : (
          <Folder
            node={tree}
            depth={0}
            collapsed={collapsed}
            toggle={toggle}
            selected={selected}
            open={open}
          />
        )}
      </div>
      <div className="file-view">
        {selected ? (
          <>
            <div className="mono muted" style={{ marginBottom: 6 }}>{selected}</div>
            <pre>{content}</pre>
          </>
        ) : (
          <div className="muted">Select a file to view its contents.</div>
        )}
      </div>
    </div>
  );
}
