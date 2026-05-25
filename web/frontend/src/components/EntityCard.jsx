import { motion } from "framer-motion";

const TYPE_LABELS = {
  person: "人物",
  item: "物品",
  location: "地点",
  concept: "概念",
};

const TYPE_COLORS = {
  person: "text-blue-400",
  item: "text-amber-400",
  location: "text-emerald-400",
  concept: "text-purple-400",
};

const IMPORTANCE_COLORS = {
  protagonist: "border-ink-accent bg-ink-accent/5",
  major: "border-ink-border-light",
  supporting: "border-ink-border",
  minor: "border-ink-border opacity-60",
};

export default function EntityCard({ entity, onClick }) {
  return (
    <motion.button
      onClick={() => onClick?.(entity)}
      whileHover={{ y: -2 }}
      className={`text-left w-full p-4 rounded-xl border bg-ink-card transition-colors hover:border-ink-accent/30 ${
        IMPORTANCE_COLORS[entity.importance] || IMPORTANCE_COLORS.supporting
      }`}
    >
      <div className="flex items-start justify-between mb-2">
        <h3 className="font-serif text-base text-ink-text font-medium">
          {entity.name}
        </h3>
        <span
          className={`text-xs font-sans px-2 py-0.5 rounded-full bg-ink-surface border border-ink-border ${
            TYPE_COLORS[entity.type] || "text-ink-text-secondary"
          }`}
        >
          {TYPE_LABELS[entity.type] || entity.type}
        </span>
      </div>
      {entity.preview && (
        <p className="text-xs text-ink-text-secondary line-clamp-2 font-sans leading-relaxed">
          {entity.preview}
        </p>
      )}
      <div className="flex items-center gap-3 mt-3 text-xs text-ink-text-muted font-sans">
        <span>状态: {entity.status}</span>
        {entity.fact_count > 0 && <span>{entity.fact_count} facts</span>}
        {entity.importance === "protagonist" && (
          <span className="text-ink-accent font-medium">主角</span>
        )}
      </div>
    </motion.button>
  );
}
