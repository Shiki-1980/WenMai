import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { getAuditSummary, getWorld, getArc } from "../lib/api";
import Terminal from "../components/Terminal";

async function saveContent(endpoint, content) {
  const res = await fetch(`/api${endpoint}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  return res.json();
}

export default function Audit() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [revise, setRevise] = useState("");
  const [target, setTarget] = useState("all");
  const [selectedArc, setSelectedArc] = useState("");
  const [terminalLines, setTerminalLines] = useState([]);
  const [isRunning, setIsRunning] = useState(false);

  // Content viewer
  const [viewer, setViewer] = useState(null); // "world" | {type:"arc", name}
  const [viewerContent, setViewerContent] = useState("");
  const [editContent, setEditContent] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [saving, setSaving] = useState(false);

  const loadSummary = async () => {
    try {
      const data = await getAuditSummary();
      setSummary(data);
      // Auto-select first arc if available
      if (data.arcs?.length > 0 && !selectedArc) {
        setSelectedArc(data.arcs[0].name);
      }
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSummary();
  }, []);

  const openViewer = async (type, arcName) => {
    if (type === "arc") {
      setViewer({ type: "arc", name: arcName });
    } else {
      setViewer(type);
    }
    setIsEditing(false);
    try {
      let data;
      if (type === "world") {
        data = await getWorld();
      } else if (type === "arc") {
        data = await getArc(arcName);
      }
      setViewerContent(data.content || "");
      setEditContent(data.content || "");
    } catch {
      setViewerContent("");
      setEditContent("");
    }
  };

  const closeViewer = () => {
    setViewer(null);
    setViewerContent("");
    setEditContent("");
    setIsEditing(false);
  };

  const handleSave = async () => {
    if (!viewer) return;
    setSaving(true);
    try {
      if (viewer === "world") {
        await saveContent("/world", editContent);
      }
      // Arc content is saved via audit revision, direct save not supported here
      setViewerContent(editContent);
      setIsEditing(false);
      loadSummary();
    } catch {
      // silent
    } finally {
      setSaving(false);
    }
  };

  const addLine = useCallback((text, type = "output") => {
    setTerminalLines((prev) => [...prev, { text, type }]);
  }, []);

  const runAudit = async () => {
    if (!revise.trim() || isRunning) return;
    if (target === "outline" && !selectedArc) return;
    setIsRunning(true);
    setTerminalLines([]);

    const arcInfo = target === "outline" ? ` [${selectedArc}]` : "";
    addLine(`$ audit --revise "${revise}" --target ${target}${arcInfo}\n`, "info");

    try {
      const res = await fetch("/api/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          revise,
          target,
          arc_name: target === "outline" ? selectedArc : "",
        }),
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.trim() || line.startsWith(":")) continue;
          if (line.startsWith("event: done")) {
            addLine("\n✓ 审阅完成\n", "info");
            break;
          }
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.text) addLine(data.text);
            } catch {
              addLine(line.slice(6));
            }
          }
        }
      }
    } catch (err) {
      addLine(`\n[ERROR] ${err.message}\n`, "error");
    } finally {
      setIsRunning(false);
      setTimeout(loadSummary, 1000);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-ink-text-muted font-sans">加载中...</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-8"
      >
        <h2 className="font-serif text-3xl font-semibold text-ink-text mb-1">
          审阅中心
        </h2>
        <p className="text-sm text-ink-text-secondary font-sans">
          查看项目摘要 · 修订内容 · 运行维护命令
        </p>
      </motion.div>

      {summary && (
        <>
          {/* World & Arcs cards */}
          <div className="grid grid-cols-2 gap-4 mb-6">
            <motion.button
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
              onClick={() => openViewer("world")}
              className="text-left bg-ink-card border border-ink-border rounded-xl p-5 hover:border-ink-accent/30 transition-colors cursor-pointer group"
            >
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-serif text-base text-ink-text">
                  世界观 {summary.world?.exists ? "✓" : "（缺失）"}
                </h3>
                <span className="text-xs text-ink-text-muted opacity-0 group-hover:opacity-100 transition-opacity font-sans">
                  点击查看全文 →
                </span>
              </div>
              <p className="text-xs text-ink-text-secondary font-sans leading-relaxed line-clamp-4">
                {summary.world?.preview || "未生成"}
              </p>
            </motion.button>

            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.15 }}
              className="bg-ink-card border border-ink-border rounded-xl p-5"
            >
              <h3 className="font-serif text-base text-ink-text mb-3">
                大纲 ({summary.arcs?.length || 0} 个)
              </h3>
              {summary.arcs?.length > 0 ? (
                <div className="space-y-1 max-h-[120px] overflow-y-auto">
                  {(summary.arcs || []).map((a) => (
                    <button
                      key={a.name}
                      onClick={() => openViewer("arc", a.name)}
                      className="w-full flex justify-between items-center text-sm font-sans bg-ink-surface hover:bg-ink-accent/10 rounded px-3 py-1.5 transition-colors group"
                    >
                      <span className="text-ink-text group-hover:text-ink-accent transition-colors">
                        {a.name}
                      </span>
                      <span className="text-ink-text-muted text-xs">
                        {a.range || a.status}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-ink-text-muted font-sans">暂无大纲</p>
              )}
            </motion.div>
          </div>

          {/* Entity Summary */}
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            className="bg-ink-card border border-ink-border rounded-xl p-5 mb-6"
          >
            <h3 className="font-serif text-base text-ink-text mb-3">
              实体概览 ({summary.entities?.length || 0} 个)
            </h3>
            <div className="grid grid-cols-3 gap-2">
              {(summary.entities || []).map((e) => (
                <div
                  key={e.name}
                  className="bg-ink-surface rounded-lg px-3 py-2 text-xs font-sans flex justify-between"
                >
                  <span className="text-ink-text">{e.name}</span>
                  <span className="text-ink-text-muted">
                    {e.type} · {e.importance}
                  </span>
                </div>
              ))}
            </div>
          </motion.div>
        </>
      )}

      {/* Revise Form */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
        className="bg-ink-card border border-ink-border rounded-xl p-5 mb-6"
      >
        <h3 className="font-serif text-base text-ink-text mb-4">修订内容</h3>

        {/* Target selector */}
        <div className="flex gap-2 mb-3">
          {[
            ["all", "全部"],
            ["world", "世界观"],
            ["entities", "实体"],
            ["outline", "大纲"],
          ].map(([value, label]) => (
            <button
              key={value}
              onClick={() => setTarget(value)}
              className={`px-3 py-1.5 text-xs font-sans rounded-md transition-colors ${
                target === value
                  ? "bg-ink-accent/15 text-ink-accent font-medium"
                  : "text-ink-text-secondary hover:text-ink-text"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Arc selector — visible when target is outline */}
        {target === "outline" && (
          <div className="mb-3">
            <select
              value={selectedArc}
              onChange={(e) => setSelectedArc(e.target.value)}
              className="w-full bg-ink-surface border border-ink-border text-ink-text text-sm rounded-lg px-3 py-2 font-sans focus:outline-none focus:border-ink-accent transition-colors"
            >
              <option value="">选择要修订的大纲...</option>
              {(summary?.arcs || []).map((a) => (
                <option key={a.name} value={a.name}>
                  {a.name} ({a.range})
                </option>
              ))}
            </select>
          </div>
        )}

        <textarea
          value={revise}
          onChange={(e) => setRevise(e.target.value)}
          placeholder="描述你想要修改的内容，例如：主角性格太弱，需要加强..."
          rows={3}
          className="w-full bg-ink-surface border border-ink-border rounded-lg px-4 py-3 text-sm text-ink-text font-sans placeholder-ink-text-muted focus:outline-none focus:border-ink-accent transition-colors resize-none mb-3"
        />
        <button
          onClick={runAudit}
          disabled={isRunning || !revise.trim() || (target === "outline" && !selectedArc)}
          className={`w-full py-2.5 rounded-lg font-sans text-sm font-medium transition-all ${
            isRunning || !revise.trim() || (target === "outline" && !selectedArc)
              ? "bg-ink-border text-ink-text-muted cursor-not-allowed"
              : "bg-ink-accent text-ink-bg hover:bg-ink-accent-hover active:scale-[0.99]"
          }`}
        >
          {isRunning
            ? "修订中..."
            : target === "outline" && selectedArc
              ? `执行修订 — ${selectedArc}`
              : "执行修订"}
        </button>
      </motion.div>

      {/* Maintenance Commands */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.35 }}
        className="bg-ink-card border border-ink-border rounded-xl p-5 mb-6"
      >
        <h3 className="font-serif text-base text-ink-text mb-4">维护命令</h3>
        <div className="flex gap-3">
          {[
            ["enrich", "补全实体卡"],
            ["worldbuild", "生成世界观"],
            ["init-schema", "生成Schema"],
            ["rebuild-index", "重建索引"],
          ].map(([cmd, label]) => (
            <button
              key={cmd}
              onClick={async () => {
                if (isRunning) return;
                setIsRunning(true);
                setTerminalLines([]);
                addLine(`$ ${cmd}\n`, "info");
                try {
                  const res = await fetch(`/api/${cmd}`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: "{}",
                  });
                  if (
                    res.headers.get("content-type")?.includes("text/event-stream")
                  ) {
                    const reader = res.body.getReader();
                    const decoder = new TextDecoder();
                    let buffer = "";
                    while (true) {
                      const { done, value } = await reader.read();
                      if (done) break;
                      buffer += decoder.decode(value, { stream: true });
                      const lines = buffer.split("\n");
                      buffer = lines.pop() || "";
                      for (const line of lines) {
                        if (!line.trim() || line.startsWith(":")) continue;
                        if (line.startsWith("event: done")) {
                          addLine(`\n✓ ${label}完成\n`, "info");
                          break;
                        }
                        if (line.startsWith("data: ")) {
                          try {
                            const d = JSON.parse(line.slice(6));
                            if (d.text) addLine(d.text);
                          } catch {
                            addLine(line.slice(6));
                          }
                        }
                      }
                    }
                  } else {
                    const d = await res.json();
                    addLine(`\n✓ ${label}完成: ${JSON.stringify(d)}\n`, "info");
                  }
                } catch (err) {
                  addLine(`\n[ERROR] ${err.message}\n`, "error");
                } finally {
                  setIsRunning(false);
                  setTimeout(loadSummary, 1000);
                }
              }}
              disabled={isRunning}
              className={`flex-1 py-2.5 rounded-lg font-sans text-sm font-medium transition-all ${
                isRunning
                  ? "bg-ink-border text-ink-text-muted cursor-not-allowed"
                  : "bg-ink-surface border border-ink-border text-ink-text hover:border-ink-accent/30"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </motion.div>

      {/* Terminal */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4 }}
      >
        <Terminal
          lines={terminalLines}
          isRunning={isRunning}
          onClear={() => setTerminalLines([])}
        />
      </motion.div>

      {/* Full Content Viewer Modal */}
      <AnimatePresence>
        {viewer && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={closeViewer}
              className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
            />

            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 20 }}
              transition={{ type: "spring", duration: 0.4 }}
              className="fixed inset-0 z-50 flex items-center justify-center p-6 pointer-events-none"
            >
              <div className="pointer-events-auto bg-ink-card border border-ink-border rounded-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden shadow-2xl shadow-black/60 flex flex-col">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-ink-border shrink-0">
                  <h3 className="font-serif text-xl text-ink-text font-semibold">
                    {viewer === "world"
                      ? "世界观"
                      : `大纲 — ${viewer.name}`}
                  </h3>
                  <div className="flex items-center gap-2">
                    {viewer === "world" && (
                      !isEditing ? (
                        <button
                          onClick={() => {
                            setEditContent(viewerContent);
                            setIsEditing(true);
                          }}
                          className="px-3 py-1.5 text-xs font-sans rounded-lg bg-ink-surface border border-ink-border text-ink-text-secondary hover:text-ink-text transition-colors"
                        >
                          编辑
                        </button>
                      ) : (
                        <>
                          <button
                            onClick={handleSave}
                            disabled={saving}
                            className="px-3 py-1.5 text-xs font-sans rounded-lg bg-ink-accent text-ink-bg hover:bg-ink-accent-hover transition-colors disabled:opacity-50"
                          >
                            {saving ? "保存中..." : "保存"}
                          </button>
                          <button
                            onClick={() => {
                              setEditContent(viewerContent);
                              setIsEditing(false);
                            }}
                            className="px-3 py-1.5 text-xs font-sans rounded-lg bg-ink-surface border border-ink-border text-ink-text-secondary hover:text-ink-text transition-colors"
                          >
                            取消
                          </button>
                        </>
                      )
                    )}
                    {typeof viewer === "object" && viewer.type === "arc" && (
                      <span className="text-xs text-ink-text-muted font-sans">
                        用左侧「修订内容」的 LLM 修订来修改大纲
                      </span>
                    )}
                    <button
                      onClick={closeViewer}
                      className="w-8 h-8 flex items-center justify-center rounded-lg text-ink-text-muted hover:text-ink-text hover:bg-ink-surface transition-colors text-lg ml-1"
                    >
                      ×
                    </button>
                  </div>
                </div>

                {/* Body */}
                <div className="overflow-y-auto px-6 py-5 flex-1">
                  {typeof viewer === "object" || viewer !== "world" || !isEditing ? (
                    <div className="text-ink-text-secondary font-sans leading-relaxed whitespace-pre-wrap text-sm">
                      {viewerContent || "（无内容）"}
                    </div>
                  ) : (
                    <textarea
                      value={editContent}
                      onChange={(e) => setEditContent(e.target.value)}
                      className="w-full h-full min-h-[60vh] bg-ink-surface border border-ink-border rounded-lg px-4 py-3 text-sm text-ink-text font-sans leading-relaxed focus:outline-none focus:border-ink-accent transition-colors resize-none"
                    />
                  )}
                </div>

                {/* Footer */}
                <div className="px-6 py-3 border-t border-ink-border shrink-0 flex justify-between text-xs text-ink-text-muted font-sans">
                  <span>
                    {viewer === "world"
                      ? "plot/世界观.md"
                      : `plot/arcs/${viewer.name}.md`}
                  </span>
                  <span>
                    {viewerContent.length} 字
                  </span>
                </div>
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
