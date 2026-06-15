import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { getStatus } from "../lib/api";
import ProgressBar from "../components/ProgressBar";

const TYPE_LABELS = {
  person: "人物",
  item: "物品",
  location: "地点",
  concept: "概念",
};

const TYPE_COLORS = {
  person: { text: "text-blue-400", bg: "bg-blue-400/10", border: "border-blue-400/20" },
  item: { text: "text-amber-400", bg: "bg-amber-400/10", border: "border-amber-400/20" },
  location: { text: "text-emerald-400", bg: "bg-emerald-400/10", border: "border-emerald-400/20" },
  concept: { text: "text-purple-400", bg: "bg-purple-400/10", border: "border-purple-400/20" },
};

const STAT_ICONS = {
  "章节数": "§",
  "实体数": "◆",
  "Commits": "◎",
  "Schema": "⬡",
};

function StatCard({ label, value, sub, delay = 0 }) {
  const icon = STAT_ICONS[label] || "";
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay }}
      className="bg-ink-card border border-ink-border rounded-xl p-5 hover:border-ink-accent/30 transition-all duration-300 relative overflow-hidden group"
    >
      <div className="absolute top-0 right-0 w-20 h-20 -translate-y-1/2 translate-x-1/2 rounded-full bg-ink-accent/3 group-hover:bg-ink-accent/5 transition-colors" />
      <div className="relative">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-ink-accent-dim text-sm">{icon}</span>
          <p className="text-xs text-ink-text-muted font-sans tracking-wider">
            {label}
          </p>
        </div>
        <p className="text-3xl font-serif font-semibold text-ink-text">
          {value}
        </p>
        {sub && (
          <p className="text-xs text-ink-text-secondary mt-1.5 font-sans">{sub}</p>
        )}
      </div>
    </motion.div>
  );
}

export default function Dashboard() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getStatus()
      .then(setStatus)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <motion.div
          animate={{ opacity: [1, 0.3, 1] }}
          transition={{ repeat: Infinity, duration: 1.5 }}
          className="text-ink-text-muted font-sans"
        >
          加载中...
        </motion.div>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4">
        <p className="text-ink-text-secondary">无法加载状态</p>
        <p className="text-sm text-ink-text-muted">
          请确保后端服务已启动，且已初始化小说项目
        </p>
      </div>
    );
  }

  const novelName = status.novel?.replace("novels/", "") || "未知";

  return (
    <div className="max-w-5xl mx-auto">
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="mb-8"
      >
        <div className="flex items-baseline justify-between">
          <div>
            <h2 className="font-serif text-3xl font-semibold text-ink-text mb-1">
              {novelName}
            </h2>
            <p className="text-sm text-ink-text-secondary font-sans">
              {status.chapter_count} 章 · {status.entity_count} 实体 ·{" "}
              {status.commit_count} commits · Schema v{status.schema_version}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className={`inline-flex items-center gap-1.5 text-xs font-sans px-2.5 py-1 rounded-full ${status.chapter_count > 0 ? 'bg-ink-success/10 text-ink-success border border-ink-success/20' : 'bg-ink-surface border border-ink-border text-ink-text-muted'}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${status.chapter_count > 0 ? 'bg-ink-success' : 'bg-ink-text-muted'}`} />
              {status.chapter_count > 0 ? '创作中' : '待开始'}
            </span>
          </div>
        </div>
      </motion.div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          label="章节数"
          value={status.chapter_count}
          sub={`${status.chapters?.length || 0} 章已写`}
          delay={0}
        />
        <StatCard
          label="实体数"
          value={status.entity_count}
          sub={`${status.stub_count} stub`}
          delay={0.1}
        />
        <StatCard
          label="Commits"
          value={status.commit_count}
          sub="章节提交"
          delay={0.2}
        />
        <StatCard
          label="Schema"
          value={`v${status.schema_version}`}
          sub={status.has_world ? "世界观已就绪" : "待生成"}
          delay={0.3}
        />
      </div>

      {/* Entity Type Breakdown */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.3 }}
        className="bg-ink-card border border-ink-border rounded-xl p-5 mb-8"
      >
        <h3 className="font-serif text-lg text-ink-text mb-4">实体类型分布</h3>
        <div className="flex gap-4">
          {Object.entries(status.entity_by_type || {}).map(([type, count]) => {
            const c = TYPE_COLORS[type] || {};
            return (
              <div
                key={type}
                className={`flex-1 rounded-lg p-3.5 text-center border transition-colors ${c.bg || "bg-ink-surface"} ${c.border || "border-ink-border"}`}
              >
                <p className={`text-2xl font-serif font-semibold ${c.text || "text-ink-text"}`}>
                  {count}
                </p>
                <p className="text-xs text-ink-text-secondary mt-1 font-sans">
                  {TYPE_LABELS[type] || type}
                </p>
              </div>
            );
          })}
        </div>
      </motion.div>

      {/* Volume Progress */}
      {status.volumes && status.volumes.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.4 }}
          className="bg-ink-card border border-ink-border rounded-xl p-5 mb-8"
        >
          <h3 className="font-serif text-lg text-ink-text mb-4">分卷进度</h3>
          <div className="space-y-5">
            {status.volumes.map((vol) => (
              <div key={vol.volume}>
                <ProgressBar
                  done={vol.done}
                  total={vol.total}
                  label={`第${vol.volume}卷`}
                  size="lg"
                />
                <div className="mt-2 space-y-1">
                  {vol.arcs.map((arc) => (
                    <div
                      key={arc.name}
                      className="flex items-center justify-between text-xs font-sans pl-2"
                    >
                      <span className="text-ink-text-secondary">
                        {arc.title || arc.name}
                      </span>
                      <span className="text-ink-text-muted">
                        {arc.chapter_range} · {arc.done}/{arc.total}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </motion.div>
      )}

      {/* Quick Actions */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.5 }}
        className="flex gap-3"
      >
        <Link
          to="/write"
          className="flex-1 bg-ink-accent/10 border border-ink-accent/30 rounded-xl px-5 py-4 text-center hover:bg-ink-accent/20 transition-all duration-300 group"
        >
          <p className="font-serif text-ink-accent font-medium group-hover:scale-105 transition-transform">开始写作</p>
          <p className="text-xs text-ink-text-muted mt-1.5 font-sans">
            Plan · Write · Distill
          </p>
        </Link>
        <Link
          to="/entities"
          className="flex-1 bg-ink-surface border border-ink-border rounded-xl px-5 py-4 text-center hover:border-ink-accent/30 transition-all duration-300 group"
        >
          <p className="font-serif text-ink-text font-medium group-hover:text-ink-accent transition-colors">浏览实体</p>
          <p className="text-xs text-ink-text-muted mt-1.5 font-sans">
            {status.entity_count} 个实体
          </p>
        </Link>
        <Link
          to="/audit"
          className="flex-1 bg-ink-surface border border-ink-border rounded-xl px-5 py-4 text-center hover:border-ink-accent/30 transition-all duration-300 group"
        >
          <p className="font-serif text-ink-text font-medium group-hover:text-ink-accent transition-colors">审阅内容</p>
          <p className="text-xs text-ink-text-muted mt-1.5 font-sans">
            Audit · Enrich
          </p>
        </Link>
      </motion.div>
    </div>
  );
}
