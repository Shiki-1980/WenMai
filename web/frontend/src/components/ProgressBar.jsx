import { motion } from "framer-motion";

export default function ProgressBar({ done, total, label = "", size = "md" }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const height = size === "sm" ? "h-1.5" : size === "lg" ? "h-4" : "h-2.5";

  return (
    <div className="w-full">
      {(label || total > 0) && (
        <div className="flex justify-between text-xs text-ink-text-secondary mb-1.5 font-sans">
          <span>{label}</span>
          <span>
            {done}/{total} ({pct}%)
          </span>
        </div>
      )}
      <div className={`w-full bg-ink-border rounded-full overflow-hidden ${height}`}>
        <motion.div
          className={`h-full bg-ink-accent rounded-full`}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        />
      </div>
    </div>
  );
}
