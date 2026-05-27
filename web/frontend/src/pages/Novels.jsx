import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { listNovels, switchNovel, renameNovel } from "../lib/api";
import Terminal from "../components/Terminal";

export default function Novels() {
  const [novels, setNovels] = useState([]);
  const [active, setActive] = useState("");
  const [loading, setLoading] = useState(true);
  const [showInit, setShowInit] = useState(false);
  const [showRename, setShowRename] = useState(null);
  const [terminalLines, setTerminalLines] = useState([]);
  const [isRunning, setIsRunning] = useState(false);

  // Init form
  const [initName, setInitName] = useState("");
  const [initGenre, setInitGenre] = useState("xuanhuan");
  const [initDesc, setInitDesc] = useState("");
  const [initChapters, setInitChapters] = useState(30);
  const [initForce, setInitForce] = useState(false);

  // Rename form
  const [renameTo, setRenameTo] = useState("");

  const loadNovels = useCallback(async () => {
    try {
      const data = await listNovels();
      setNovels(data.novels);
      setActive(data.active);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadNovels();
  }, [loadNovels]);

  const addLine = useCallback((text, type = "output") => {
    setTerminalLines((prev) => [...prev, { text, type }]);
  }, []);

  const runInit = async (e) => {
    e.preventDefault();
    if (!initDesc.trim() || isRunning) return;
    setIsRunning(true);
    setTerminalLines([]);
    addLine(`$ init --desc "${initDesc}" --genre ${initGenre}\n`, "info");

    try {
      const res = await fetch("/api/novels/init", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: initName,
          genre: initGenre,
          desc: initDesc,
          chapters: initChapters,
          force: initForce,
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
            addLine("\n✓ 小说初始化完成！\n", "info");
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
    } catch (err) {
      addLine(`\n[ERROR] ${err.message}\n`, "error");
    } finally {
      setIsRunning(false);
      setShowInit(false);
      loadNovels();
    }
  };

  const handleSwitch = async (name) => {
    try {
      await switchNovel(name);
      loadNovels();
    } catch {
      // silent
    }
  };

  const handleRename = async (oldName) => {
    if (!renameTo.trim()) return;
    try {
      await renameNovel(oldName, renameTo);
      setShowRename(null);
      setRenameTo("");
      loadNovels();
    } catch {
      // silent
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
        className="mb-8 flex items-center justify-between"
      >
        <div>
          <h2 className="font-serif text-3xl font-semibold text-ink-text mb-1">
            小说管理
          </h2>
          <p className="text-sm text-ink-text-secondary font-sans">
            {novels.length} 部小说
          </p>
        </div>
        <button
          onClick={() => setShowInit(!showInit)}
          className={`px-4 py-2.5 rounded-lg font-sans text-sm font-medium transition-all ${
            showInit
              ? "bg-ink-surface border border-ink-border text-ink-text-secondary"
              : "bg-ink-accent text-ink-bg hover:bg-ink-accent-hover"
          }`}
        >
          {showInit ? "取消" : "+ 新建小说"}
        </button>
      </motion.div>

      {/* Init Form */}
      <AnimatePresence>
        {showInit && (
          <motion.form
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden mb-6"
            onSubmit={runInit}
          >
            <div className="bg-ink-card border border-ink-border rounded-xl p-5 space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                    小说名称（留空自动生成）
                  </label>
                  <input
                    type="text"
                    value={initName}
                    onChange={(e) => setInitName(e.target.value)}
                    placeholder="LLM 自动命名"
                    className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-sans placeholder-ink-text-muted focus:outline-none focus:border-ink-accent transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                    题材
                  </label>
                  <input
                    type="text"
                    value={initGenre}
                    onChange={(e) => setInitGenre(e.target.value)}
                    list="genre-suggestions"
                    className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-sans focus:outline-none focus:border-ink-accent transition-colors"
                    placeholder="如：玄幻、仙侠、都市、科幻、悬疑..."
                  />
                  <datalist id="genre-suggestions">
                    <option value="xuanhuan">玄幻 / Xuan Huan</option>
                    <option value="xianxia">仙侠 / Xian Xia</option>
                    <option value="urban">都市 / Urban</option>
                    <option value="scifi">科幻 / Sci-Fi</option>
                    <option value="suspense">悬疑 / Suspense</option>
                    <option value="post-apocalyptic">末世 / Post-Apocalyptic</option>
                    <option value="western-fantasy">西幻 / Western Fantasy</option>
                    <option value="historical">历史 / Historical</option>
                    <option value="infinite-stream">无限流 / Infinite Stream</option>
                    <option value="light-novel">轻小说 / Light Novel</option>
                  </datalist>
                </div>
              </div>
              <div>
                <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                  故事描述 <span className="text-ink-error">*</span>
                </label>
                <textarea
                  value={initDesc}
                  onChange={(e) => setInitDesc(e.target.value)}
                  placeholder="一句话描述你的故事，例如：重生归来的炼丹鬼才，隐于市井开一间小药铺..."
                  rows={2}
                  className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-sans placeholder-ink-text-muted focus:outline-none focus:border-ink-accent transition-colors resize-none"
                  required
                />
              </div>
              <div className="flex items-center gap-4">
                <div className="w-32">
                  <label className="block text-xs text-ink-text-secondary mb-1.5 font-sans">
                    章节数
                  </label>
                  <input
                    type="number"
                    value={initChapters}
                    onChange={(e) => setInitChapters(parseInt(e.target.value) || 30)}
                    className="w-full bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-mono focus:outline-none focus:border-ink-accent transition-colors"
                  />
                </div>
                <label className="flex items-center gap-2 pt-5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={initForce}
                    onChange={(e) => setInitForce(e.target.checked)}
                    className="w-4 h-4 rounded border-ink-border bg-ink-surface accent-ink-accent"
                  />
                  <span className="text-sm text-ink-text-secondary font-sans">
                    覆盖已有文件
                  </span>
                </label>
              </div>
              <button
                type="submit"
                disabled={isRunning || !initDesc.trim()}
                className={`w-full py-2.5 rounded-lg font-sans text-sm font-medium transition-all ${
                  isRunning || !initDesc.trim()
                    ? "bg-ink-border text-ink-text-muted cursor-not-allowed"
                    : "bg-ink-accent text-ink-bg hover:bg-ink-accent-hover"
                }`}
              >
                {isRunning ? "初始化中..." : "开始初始化"}
              </button>
            </div>
          </motion.form>
        )}
      </AnimatePresence>

      {/* Novel List */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="space-y-3 mb-6"
      >
        {novels.length === 0 ? (
          <div className="text-center py-16">
            <p className="text-ink-text-muted text-lg font-serif mb-2">
              还没有小说项目
            </p>
            <p className="text-sm text-ink-text-secondary font-sans">
              点击"新建小说"开始创作之旅
            </p>
          </div>
        ) : (
          novels.map((novel, i) => (
            <motion.div
              key={novel.name}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.05 * i }}
              className={`bg-ink-card border rounded-xl p-5 transition-colors ${
                novel.is_active
                  ? "border-ink-accent/40"
                  : "border-ink-border"
              }`}
            >
              <div className="flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-3 mb-1">
                    <h3 className="font-serif text-lg text-ink-text font-medium">
                      {novel.name}
                    </h3>
                    {novel.is_active && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-ink-accent/15 text-ink-accent font-sans font-medium">
                        当前
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-ink-text-muted font-sans">
                    {novel.chapter_count} 章 · {novel.entity_count} 实体
                  </p>
                </div>
                <div className="flex gap-2">
                  {!novel.is_active && (
                    <button
                      onClick={() => handleSwitch(novel.name)}
                      className="px-4 py-2 rounded-lg bg-ink-surface border border-ink-border text-ink-text-secondary text-sm font-sans hover:text-ink-text hover:border-ink-accent/30 transition-all"
                    >
                      切换到此小说
                    </button>
                  )}
                  <button
                    onClick={() => {
                      setShowRename(novel.name);
                      setRenameTo("");
                    }}
                    className="px-3 py-2 rounded-lg text-ink-text-muted text-sm font-sans hover:text-ink-text transition-colors"
                  >
                    重命名
                  </button>
                </div>
              </div>

              {/* Inline Rename */}
              <AnimatePresence>
                {showRename === novel.name && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    className="overflow-hidden"
                  >
                    <div className="flex gap-2 mt-3 pt-3 border-t border-ink-border">
                      <input
                        type="text"
                        value={renameTo}
                        onChange={(e) => setRenameTo(e.target.value)}
                        placeholder="新名称..."
                        className="flex-1 bg-ink-surface border border-ink-border rounded-lg px-3 py-2 text-sm text-ink-text font-sans placeholder-ink-text-muted focus:outline-none focus:border-ink-accent transition-colors"
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleRename(novel.name);
                          if (e.key === "Escape") setShowRename(null);
                        }}
                      />
                      <button
                        onClick={() => handleRename(novel.name)}
                        className="px-4 py-2 bg-ink-accent text-ink-bg rounded-lg text-sm font-sans font-medium hover:bg-ink-accent-hover transition-colors"
                      >
                        确认
                      </button>
                      <button
                        onClick={() => setShowRename(null)}
                        className="px-4 py-2 bg-ink-surface border border-ink-border text-ink-text-secondary rounded-lg text-sm font-sans hover:text-ink-text transition-colors"
                      >
                        取消
                      </button>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          ))
        )}
      </motion.div>

      {/* Terminal for init output */}
      <Terminal
        lines={terminalLines}
        isRunning={isRunning}
        onClear={() => setTerminalLines([])}
      />
    </div>
  );
}
