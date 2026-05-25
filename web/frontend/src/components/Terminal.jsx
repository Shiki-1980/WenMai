import { useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";

export default function Terminal({ lines = [], isRunning = false, onClear }) {
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);

  return (
    <div className="relative bg-ink-bg border border-ink-border rounded-xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-ink-surface border-b border-ink-border">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-ink-error" />
          <span className="w-2.5 h-2.5 rounded-full bg-ink-warn" />
          <span className="w-2.5 h-2.5 rounded-full bg-ink-success" />
        </div>
        <span className="text-xs text-ink-text-muted font-mono">终端输出</span>
        <button
          onClick={onClear}
          className="text-xs text-ink-text-muted hover:text-ink-text transition-colors font-sans px-2"
        >
          清空
        </button>
      </div>

      {/* Content */}
      <div className="terminal-scroll h-80 overflow-y-auto p-4 font-mono text-sm leading-relaxed">
        {lines.length === 0 && !isRunning && (
          <p className="text-ink-text-muted italic">
            运行命令后，输出将显示在这里...
          </p>
        )}
        <AnimatePresence initial={false}>
          {lines.map((line, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.15 }}
              className={`whitespace-pre-wrap break-all ${
                line.type === "error"
                  ? "text-ink-error"
                  : line.type === "warn"
                  ? "text-ink-warn"
                  : line.type === "info"
                  ? "text-ink-accent"
                  : "text-ink-text-secondary"
              }`}
            >
              {line.text}
            </motion.div>
          ))}
        </AnimatePresence>
        {isRunning && (
          <motion.span
            animate={{ opacity: [1, 0] }}
            transition={{ repeat: Infinity, duration: 0.6 }}
            className="inline-block w-2 h-4 bg-ink-accent ml-0.5 align-text-bottom"
          />
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
