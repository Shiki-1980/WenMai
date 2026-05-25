import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { listChapters, getChapter } from "../lib/api";

export default function Chapters() {
  const [chapters, setChapters] = useState([]);
  const [selected, setSelected] = useState(null);
  const [content, setContent] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listChapters()
      .then((data) => setChapters(data.chapters || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const openChapter = async (ch) => {
    setSelected(ch);
    setContent(null);
    try {
      const d = await getChapter(ch.number);
      setContent(d);
    } catch {
      setContent(null);
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
    <div className="max-w-6xl mx-auto">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-8"
      >
        <h2 className="font-serif text-3xl font-semibold text-ink-text mb-1">
          章节浏览
        </h2>
        <p className="text-sm text-ink-text-secondary font-sans">
          {chapters.length} 章已生成
        </p>
      </motion.div>

      {chapters.length === 0 ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="text-center py-16"
        >
          <p className="text-ink-text-muted text-lg font-serif mb-2">
            还没有章节
          </p>
          <p className="text-sm text-ink-text-secondary font-sans">
            运行规划命令创建篇章大纲，然后开始写作
          </p>
        </motion.div>
      ) : (
        <div className="flex gap-6">
          {/* Chapter List */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="w-72 shrink-0 space-y-1"
          >
            {chapters.map((ch, i) => (
              <motion.button
                key={ch.number}
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.02 * Math.min(i, 30) }}
                onClick={() => openChapter(ch)}
                className={`w-full text-left px-4 py-3 rounded-lg font-sans text-sm transition-all ${
                  selected?.number === ch.number
                    ? "bg-ink-accent/10 border border-ink-accent/30 text-ink-accent"
                    : "text-ink-text-secondary hover:text-ink-text hover:bg-ink-card border border-transparent"
                }`}
              >
                <span className="font-mono text-xs text-ink-text-muted mr-2">
                  ch.{ch.number}
                </span>
                {ch.title}
              </motion.button>
            ))}
          </motion.div>

          {/* Chapter Content */}
          <div className="flex-1">
            <AnimatePresence mode="wait">
              {content ? (
                <motion.div
                  key={content.number}
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -12 }}
                  className="bg-ink-card border border-ink-border rounded-xl p-8"
                >
                  <h3 className="font-serif text-2xl text-ink-text font-semibold mb-2">
                    {content.title || `第${content.number}章`}
                  </h3>
                  <p className="text-xs text-ink-text-muted mb-6 font-sans">
                    第 {content.number} 章 · {content.created || "未知日期"} ·{" "}
                    {content.content?.length || 0} 字
                  </p>

                  {/* Summary */}
                  {content.summary && (
                    <div className="bg-ink-surface border border-ink-border rounded-lg p-4 mb-6">
                      <p className="text-xs text-ink-text-secondary mb-2 font-sans tracking-wider">
                        摘要
                      </p>
                      <p className="text-sm text-ink-text-secondary font-sans leading-relaxed">
                        {content.summary}
                      </p>
                    </div>
                  )}

                  {/* Chapter Body */}
                  <div className="prose prose-invert max-w-none">
                    <div className="text-ink-text font-serif leading-loose text-[15px] whitespace-pre-wrap">
                      {content.content}
                    </div>
                  </div>
                </motion.div>
              ) : selected ? (
                <div className="flex items-center justify-center h-64">
                  <p className="text-ink-text-muted font-sans">加载章节...</p>
                </div>
              ) : (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="flex flex-col items-center justify-center h-64 text-center"
                >
                  <div className="text-5xl mb-4 opacity-20">§</div>
                  <p className="text-ink-text-muted font-sans">
                    选择左侧章节查看内容
                  </p>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      )}
    </div>
  );
}
