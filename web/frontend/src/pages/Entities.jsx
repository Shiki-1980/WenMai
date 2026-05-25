import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { listEntities, getEntity } from "../lib/api";
import EntityCard from "../components/EntityCard";

const TYPE_LABELS = {
  person: "人物",
  item: "物品",
  location: "地点",
  concept: "概念",
};

export default function Entities() {
  const [entities, setEntities] = useState([]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listEntities()
      .then((data) => setEntities(data.entities || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const openDetail = async (entity) => {
    setSelected(entity);
    setDetail(null);
    document.body.style.overflow = "hidden";
    try {
      const d = await getEntity(entity.type, entity.name);
      setDetail(d);
    } catch {
      setDetail(null);
    }
  };

  const closeDetail = () => {
    setSelected(null);
    setDetail(null);
    document.body.style.overflow = "";
  };

  const filtered = entities.filter((e) => {
    if (filter && e.type !== filter) return false;
    if (search && !e.name.includes(search)) return false;
    return true;
  });

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
          实体图鉴
        </h2>
        <p className="text-sm text-ink-text-secondary font-sans">
          {entities.length} 个实体 · 人物 · 物品 · 地点 · 概念
        </p>
      </motion.div>

      {/* Filters */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        className="flex gap-3 mb-6"
      >
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索实体..."
          className="flex-1 bg-ink-card border border-ink-border rounded-lg px-4 py-2.5 text-sm text-ink-text font-sans placeholder-ink-text-muted focus:outline-none focus:border-ink-accent transition-colors"
        />
        <div className="flex gap-1 bg-ink-card border border-ink-border rounded-lg p-1">
          {[
            ["", "全部"],
            ["person", "人物"],
            ["item", "物品"],
            ["location", "地点"],
            ["concept", "概念"],
          ].map(([value, label]) => (
            <button
              key={value}
              onClick={() => setFilter(value)}
              className={`px-3 py-1.5 text-xs font-sans rounded-md transition-colors ${
                filter === value
                  ? "bg-ink-accent/15 text-ink-accent font-medium"
                  : "text-ink-text-secondary hover:text-ink-text"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </motion.div>

      {/* Entity Grid */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.2 }}
        className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3"
      >
        {filtered.map((entity, i) => (
          <motion.div
            key={`${entity.type}/${entity.name}`}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.02 * Math.min(i, 30) }}
          >
            <EntityCard entity={entity} onClick={openDetail} />
          </motion.div>
        ))}
        {filtered.length === 0 && (
          <p className="text-ink-text-muted col-span-full text-center py-12 font-sans">
            没有找到匹配的实体
          </p>
        )}
      </motion.div>

      {/* Floating Modal */}
      <AnimatePresence>
        {selected && (
          <>
            {/* Backdrop */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={closeDetail}
              className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
            />

            {/* Modal */}
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 20 }}
              transition={{ type: "spring", duration: 0.4 }}
              className="fixed inset-0 z-50 flex items-center justify-center p-6 pointer-events-none"
            >
              <div className="pointer-events-auto bg-ink-card border border-ink-border rounded-2xl w-full max-w-2xl max-h-[85vh] overflow-hidden shadow-2xl shadow-black/60 flex flex-col">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-ink-border shrink-0">
                  <div className="flex items-center gap-3">
                    <h3 className="font-serif text-xl text-ink-text font-semibold">
                      {selected.name}
                    </h3>
                    <span className="text-xs px-2 py-0.5 rounded-full bg-ink-surface border border-ink-border text-ink-text-secondary font-sans">
                      {TYPE_LABELS[selected.type] || selected.type}
                    </span>
                    <span className="text-xs text-ink-text-muted font-sans">
                      {selected.importance}
                    </span>
                    {selected.status === "stub" && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-ink-warn/15 text-ink-warn font-sans">
                        STUB
                      </span>
                    )}
                  </div>
                  <button
                    onClick={closeDetail}
                    className="w-8 h-8 flex items-center justify-center rounded-lg text-ink-text-muted hover:text-ink-text hover:bg-ink-surface transition-colors text-lg"
                  >
                    ×
                  </button>
                </div>

                {/* Body */}
                <div className="overflow-y-auto px-6 py-5 flex-1">
                  {detail ? (
                    <div className="space-y-5">
                      {/* Facts */}
                      {detail.facts && detail.facts.length > 0 && (
                        <div>
                          <h4 className="text-xs text-ink-text-secondary mb-3 font-sans tracking-wider uppercase">
                            Facts ({detail.facts.length})
                          </h4>
                          <div className="grid grid-cols-2 gap-2">
                            {detail.facts.map((f, i) => (
                              <div
                                key={i}
                                className="bg-ink-surface rounded-lg px-3 py-2.5 text-sm"
                              >
                                <span className="text-ink-accent font-medium font-sans">
                                  {f.predicate}
                                </span>
                                <span className="text-ink-text-secondary mx-2">:</span>
                                <span className="text-ink-text">{f.object}</span>
                                {f.since_chapter > 0 && (
                                  <span className="text-ink-text-muted text-xs ml-2">
                                    ch.{f.since_chapter}
                                  </span>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Content */}
                      {detail.content && (
                        <div>
                          <h4 className="text-xs text-ink-text-secondary mb-3 font-sans tracking-wider uppercase">
                            实体卡
                          </h4>
                          <div className="text-ink-text-secondary font-sans leading-relaxed whitespace-pre-wrap text-sm">
                            {detail.content}
                          </div>
                        </div>
                      )}

                      {/* Appears in */}
                      {detail.appears_in && detail.appears_in.length > 0 && (
                        <div className="pt-4 border-t border-ink-border">
                          <h4 className="text-xs text-ink-text-secondary mb-3 font-sans tracking-wider uppercase">
                            出现章节
                          </h4>
                          <div className="flex flex-wrap gap-1.5">
                            {detail.appears_in.map((ch) => (
                              <span
                                key={ch}
                                className="text-xs px-2 py-1 rounded bg-ink-surface text-ink-text-secondary font-mono"
                              >
                                ch.{ch}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="flex items-center justify-center py-16">
                      <motion.div
                        animate={{ opacity: [1, 0.3, 1] }}
                        transition={{ repeat: Infinity, duration: 1.2 }}
                        className="text-ink-text-muted font-sans text-sm"
                      >
                        加载详情中...
                      </motion.div>
                    </div>
                  )}
                </div>

                {/* Footer */}
                <div className="px-6 py-3 border-t border-ink-border shrink-0 flex justify-between text-xs text-ink-text-muted font-sans">
                  <span>
                    更新于 {detail?.updated || "—"}
                  </span>
                  <span>
                    enriched_through ch.{detail?.enriched_through || 0}
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
