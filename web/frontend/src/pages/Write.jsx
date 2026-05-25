import { useState, useEffect, useRef, useCallback } from "react";
import { motion } from "framer-motion";
import { listArcs, getStatus } from "../lib/api";
import Terminal from "../components/Terminal";

export default function Write() {
  const [arcs, setArcs] = useState([]);
  const [status, setStatus] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [terminalLines, setTerminalLines] = useState([]);
  const [isRunning, setIsRunning] = useState(false);
  const [taskId, setTaskId] = useState("");
  const [inputPrompt, setInputPrompt] = useState("");
  const [showInput, setShowInput] = useState(false);
  const eventSourceRef = useRef(null);
  const inputRef = useRef(null);

  // Command form state
  const [cmd, setCmd] = useState("plan");
  const [direction, setDirection] = useState("");
  const [arcName, setArcName] = useState("");
  const [wordCount, setWordCount] = useState(3000);
  const [chapterNum, setChapterNum] = useState("");
  const [chapterOutline, setChapterOutline] = useState("");
  const [numChapters, setNumChapters] = useState(30);
  const [force, setForce] = useState(false);
  const [volumeNum, setVolumeNum] = useState(0);

  const loadData = useCallback(async () => {
    setLoadError("");
    try {
      const [arcsData, statusData] = await Promise.all([listArcs(), getStatus()]);
      setArcs(arcsData.arcs || []);
      setStatus(statusData);
    } catch (err) {
      setLoadError(err.message || "无法连接到服务器");
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Select an arc from the list — switches to "write" tab and fills the field
  const selectArc = (name) => {
    setCmd("write");
    setArcName(name);
  };

  const addLine = useCallback((text, type = "output") => {
    setTerminalLines((prev) => [...prev, { text, type }]);
  }, []);

  const runCommand = async (endpoint, body) => {
    setIsRunning(true);
    setShowInput(false);
    const tid = `${endpoint.replace("/", "_")}_${Date.now()}`;
    setTaskId(tid);

    try {
      const res = await fetch(`/api${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
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

          if (line.startsWith("event: input")) {
            continue;
          }

          if (line.startsWith("event: done")) {
            addLine("\n✓ 命令执行完成\n", "info");
            break;
          }

          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.text) {
                addLine(data.text);
              }
              if (data.prompt) {
                setInputPrompt(data.prompt);
                setShowInput(true);
              }
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
      setShowInput(false);
      // Refresh data after command completes
      setTimeout(loadData, 500);
    }
  };

  const respondToPrompt = async (value) => {
    setShowInput(false);
    addLine(`>>> ${value}`, "info");
    try {
      await fetch(`/api/respond/${taskId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
      });
    } catch {
      // continue
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (isRunning) return;

    setTerminalLines([]);

    switch (cmd) {
      case "plan":
        addLine(`$ plan --direction "${direction}" --num-chapters ${numChapters}\n`, "info");
        runCommand("/plan", {
          direction,
          num_chapters: numChapters,
          volume: volumeNum,
        });
        break;
      case "write":
        if (!arcName) {
          addLine("错误：请先选择一个篇章\n", "error");
          return;
        }
        addLine(`$ write --arc "${arcName}" --words ${wordCount} --yes\n`, "info");
        runCommand("/write", {
          arc: arcName,
          words: wordCount,
          force,
          yes: true,
        });
        break;
      case "write-one":
        addLine(`$ write-one --chapter ${chapterNum} --words ${wordCount}\n`, "info");
        runCommand("/write-one", {
          chapter: parseInt(chapterNum) || 0,
          outline: chapterOutline,
          words: wordCount,
        });
        break;
      case "distill":
        addLine(`$ distill --chapter ${chapterNum}\n`, "info");
        runCommand("/distill", {
          chapter: parseInt(chapterNum) || 0,
        });
        break;
      default:
        break;
    }
  };

  const isWriteCommand = cmd === "write";

  return (
    <div className="max-w-4xl mx-auto">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-8"
      >
        <h2 className="font-serif text-3xl font-semibold text-ink-text mb-1">
          写作
        </h2>
        <p className="text-sm text-ink-text-secondary font-sans">
          Plan · Write · Distill — 生成大纲并逐章写作
        </p>
      </motion.div>

      {/* ── Arc List (clickable, above the form) ── */}
      {loadError ? (
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-ink-error/10 border border-ink-error/30 rounded-xl p-5 mb-6"
        >
          <p className="text-ink-error text-sm font-sans mb-2">
            加载失败: {loadError}
          </p>
          <button
            onClick={loadData}
            className="px-4 py-1.5 bg-ink-surface border border-ink-border rounded-lg text-xs text-ink-text-secondary hover:text-ink-text transition-colors font-sans"
          >
            重试
          </button>
        </motion.div>
      ) : arcs.length > 0 ? (
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="bg-ink-card border border-ink-border rounded-xl p-5 mb-6"
        >
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-serif text-base text-ink-text">篇章列表</h3>
            <span className="text-xs text-ink-text-muted font-sans">
              点击选择 → 自动填入批量写作
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {arcs.map((arc) => (
              <button
                key={arc.name}
                type="button"
                onClick={() => selectArc(arc.name)}
                className={`text-left bg-ink-surface rounded-lg px-4 py-3 text-sm font-sans flex justify-between items-center transition-all border hover:border-ink-accent/40 ${
                  arcName === arc.name && cmd === "write"
                    ? "border-ink-accent/60 bg-ink-accent/5"
                    : "border-transparent"
                }`}
              >
                <span
                  className={
                    arcName === arc.name && cmd === "write"
                      ? "text-ink-accent font-medium"
                      : "text-ink-text"
                  }
                >
                  {arc.title || arc.name}
                </span>
                <span className="text-xs text-ink-text-muted font-mono">
                  {arc.chapter_range}
                </span>
              </button>
            ))}
          </div>
        </motion.div>
      ) : (
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-ink-card border border-ink-border rounded-xl p-5 mb-6 text-center"
        >
          <p className="text-ink-text-muted font-sans text-sm">
            还没有篇章大纲 — 使用「规划大纲」来创建
          </p>
        </motion.div>
      )}

      {/* ── Command Form ── */}
      <motion.form
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        onSubmit={handleSubmit}
        className="bg-ink-card border border-ink-border rounded-xl p-5 mb-6"
      >
        {/* Command Tabs */}
        <div className="flex gap-1 mb-5 bg-ink-surface rounded-lg p-1">
          {[
            ["plan", "规划大纲"],
            ["write", "批量写作"],
            ["write-one", "写单章"],
            ["distill", "重新蒸馏"],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setCmd(value)}
              className={`flex-1 py-2 text-sm font-sans rounded-md transition-colors ${
                cmd === value
                  ? "bg-ink-accent/15 text-ink-accent font-medium"
                  : "text-ink-text-secondary hover:text-ink-text"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="space-y-4">
          {/* Plan fields */}
          {cmd === "plan" && (
            <>
              <div>
                <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                  本卷方向 <span className="text-ink-error">*</span>
                </label>
                <textarea
                  value={direction}
                  onChange={(e) => setDirection(e.target.value)}
                  placeholder="例如：叶凡离开青云宗前往中州，途中遭遇魔道追杀..."
                  rows={2}
                  className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-sans placeholder-ink-text-muted focus:outline-none focus:border-ink-accent transition-colors resize-none"
                  required
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                    章节数（0=LLM决定）
                  </label>
                  <input
                    type="number"
                    value={numChapters}
                    onChange={(e) => setNumChapters(parseInt(e.target.value) || 0)}
                    className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-mono focus:outline-none focus:border-ink-accent transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                    卷号（0=自动检测）
                  </label>
                  <input
                    type="number"
                    value={volumeNum}
                    onChange={(e) => setVolumeNum(parseInt(e.target.value) || 0)}
                    className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-mono focus:outline-none focus:border-ink-accent transition-colors"
                  />
                </div>
              </div>
            </>
          )}

          {/* Write fields */}
          {isWriteCommand && (
            <>
              <div>
                <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                  篇章 (Arc) <span className="text-ink-error">*</span>
                </label>
                <select
                  value={arcName}
                  onChange={(e) => setArcName(e.target.value)}
                  className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-sans focus:outline-none focus:border-ink-accent transition-colors cursor-pointer"
                  required
                >
                  <option value="">选择篇章...</option>
                  {arcs.map((a) => (
                    <option key={a.name} value={a.name}>
                      {a.title || a.name} ({a.chapter_range})
                    </option>
                  ))}
                </select>
                {arcs.length === 0 && !loadError && (
                  <p className="text-xs text-ink-text-muted mt-1.5 font-sans">
                    还没有篇章 — 请先使用「规划大纲」创建
                  </p>
                )}
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                    每章字数
                  </label>
                  <input
                    type="number"
                    value={wordCount}
                    onChange={(e) => setWordCount(parseInt(e.target.value) || 3000)}
                    className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-mono focus:outline-none focus:border-ink-accent transition-colors"
                  />
                </div>
                <div className="flex items-end pb-1">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={force}
                      onChange={(e) => setForce(e.target.checked)}
                      className="w-4 h-4 rounded border-ink-border bg-ink-surface accent-ink-accent"
                    />
                    <span className="text-sm text-ink-text-secondary font-sans">
                      强制重写已有章节
                    </span>
                  </label>
                </div>
              </div>
            </>
          )}

          {/* Write-one fields */}
          {cmd === "write-one" && (
            <>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                    章节号 <span className="text-ink-error">*</span>
                  </label>
                  <input
                    type="number"
                    value={chapterNum}
                    onChange={(e) => setChapterNum(e.target.value)}
                    className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-mono focus:outline-none focus:border-ink-accent transition-colors"
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                    字数
                  </label>
                  <input
                    type="number"
                    value={wordCount}
                    onChange={(e) => setWordCount(parseInt(e.target.value) || 3000)}
                    className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-mono focus:outline-none focus:border-ink-accent transition-colors"
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                  章节概要
                </label>
                <textarea
                  value={chapterOutline}
                  onChange={(e) => setChapterOutline(e.target.value)}
                  placeholder="例如：叶凡突破金丹四层..."
                  rows={2}
                  className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-sans placeholder-ink-text-muted focus:outline-none focus:border-ink-accent transition-colors resize-none"
                />
              </div>
            </>
          )}

          {/* Distill fields */}
          {cmd === "distill" && (
            <div>
              <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                章节号 <span className="text-ink-error">*</span>
              </label>
              <input
                type="number"
                value={chapterNum}
                onChange={(e) => setChapterNum(e.target.value)}
                className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-mono focus:outline-none focus:border-ink-accent transition-colors"
                required
              />
            </div>
          )}
        </div>

        {/* Submit */}
        <button
          type="submit"
          disabled={isRunning}
          className={`mt-5 w-full py-2.5 rounded-lg font-sans text-sm font-medium transition-all ${
            isRunning
              ? "bg-ink-border text-ink-text-muted cursor-not-allowed"
              : "bg-ink-accent text-ink-bg hover:bg-ink-accent-hover active:scale-[0.99]"
          }`}
        >
          {isRunning ? "执行中..." : "运行命令"}
        </button>
      </motion.form>

      {/* Input Prompt Bar */}
      {showInput && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 bg-ink-card border border-ink-accent/40 rounded-xl p-4 shadow-2xl shadow-black/50 max-w-lg w-full"
        >
          <p className="text-sm text-ink-text-secondary mb-2 font-sans">
            {inputPrompt || "等待输入..."}
          </p>
          <div className="flex gap-2">
            <input
              ref={inputRef}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter") respondToPrompt(e.target.value);
              }}
              className="flex-1 bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-sans focus:outline-none focus:border-ink-accent"
              placeholder="输入回复..."
            />
            <button
              onClick={() => {
                if (inputRef.current) respondToPrompt(inputRef.current.value);
              }}
              className="px-4 py-2 bg-ink-accent text-ink-bg rounded-lg text-sm font-sans font-medium hover:bg-ink-accent-hover transition-colors"
            >
              发送
            </button>
            <button
              onClick={() => respondToPrompt("n")}
              className="px-4 py-2 bg-ink-surface border border-ink-border text-ink-text-secondary rounded-lg text-sm font-sans hover:text-ink-text transition-colors"
            >
              跳过
            </button>
          </div>
        </motion.div>
      )}

      {/* Terminal */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
      >
        <Terminal
          lines={terminalLines}
          isRunning={isRunning}
          onClear={() => setTerminalLines([])}
        />
      </motion.div>
    </div>
  );
}
