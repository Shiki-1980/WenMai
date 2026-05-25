import { useState, useEffect } from "react";
import { motion } from "framer-motion";
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
    try {
      const d = await getEntity(entity.type, entity.name);
      setDetail(d);
    } catch {
      setDetail(null);
    }
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

      {/* Entity Grid + Detail */}
      <div className="flex gap-6">
        {/* Grid */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.2 }}
          className={`grid grid-cols-1 sm:grid-cols-2 gap-3 ${
            selected ? "flex-1" : "w-full"
          }`}
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
            <p className="text-ink-text-muted col-span-2 text-center py-12 font-sans">
              没有找到匹配的实体
            </p>
          )}
        </motion.div>

        {/* Detail Panel */}
        {selected && (
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            className="w-96 shrink-0 bg-ink-card border border-ink-border rounded-xl p-5 max-h-[70vh] overflow-y-auto"
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-serif text-xl text-ink-text font-semibold">
                {selected.name}
              </h3>
              <button
                onClick={() => {
                  setSelected(null);
                  setDetail(null);
                }}
                className="text-ink-text-muted hover:text-ink-text transition-colors text-lg"
              >
                ×
              </button>
            </div>

            <div className="flex items-center gap-2 mb-4">
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

            {detail ? (
              <>
                {/* Facts */}
                {detail.facts && detail.facts.length > 0 && (
                  <div className="mb-4">
                    <h4 className="text-xs text-ink-text-secondary mb-2 font-sans tracking-wider uppercase">
                      Facts ({detail.facts.length})
                    </h4>
                    <div className="space-y-1.5">
                      {detail.facts.map((f, i) => (
                        <div
                          key={i}
                          className="bg-ink-surface rounded-lg px-3 py-2 text-sm"
                        >
                          <span className="text-ink-accent font-medium font-sans">
                            {f.predicate}
                          </span>
                          <span className="text-ink-text-secondary mx-2">
                            :
                          </span>
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
                    <h4 className="text-xs text-ink-text-secondary mb-2 font-sans tracking-wider uppercase">
                      实体卡
                    </h4>
                    <div className="prose prose-invert prose-sm max-w-none text-ink-text-secondary font-sans leading-relaxed whitespace-pre-wrap">
                      {detail.content.slice(0, 2000)}
                    </div>
                  </div>
                )}

                {/* Appears in */}
                {detail.appears_in && detail.appears_in.length > 0 && (
                  <div className="mt-4 pt-4 border-t border-ink-border">
                    <h4 className="text-xs text-ink-text-secondary mb-2 font-sans tracking-wider uppercase">
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
              </>
            ) : (
              <p className="text-sm text-ink-text-muted font-sans">
                加载详情中...
              </p>
            )}
          </motion.div>
        )}
      </div>
    </div>
  );
}
