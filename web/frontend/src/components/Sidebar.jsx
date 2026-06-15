import { useState, useEffect } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { getConfig, listNovels, switchNovel } from "../lib/api";

const NAV_ITEMS = [
  { to: "/", label: "总览", icon: "◇" },
  { to: "/write", label: "写作", icon: "✦" },
  { to: "/entities", label: "实体", icon: "◆" },
  { to: "/chapters", label: "章节", icon: "§" },
  { to: "/audit", label: "审阅", icon: "◎" },
  { to: "/novels", label: "小说管理", icon: "▤" },
  { to: "/settings", label: "设置", icon: "⚙" },
];

export default function Sidebar() {
  const [novels, setNovels] = useState([]);
  const [activeNovel, setActiveNovel] = useState("");
  const navigate = useNavigate();

  const loadNovels = async () => {
    try {
      const data = await listNovels();
      setNovels(data.novels);
      const active = data.novels.find((n) => n.is_active);
      setActiveNovel(active?.name || data.novels[0]?.name || "");
    } catch {
      // Silent fail
    }
  };

  useEffect(() => {
    loadNovels();
  }, []);

  return (
    <aside className="w-[260px] shrink-0 h-full bg-ink-surface border-r border-ink-border flex flex-col">
      {/* Logo */}
      <div className="px-6 py-5 border-b border-ink-border">
        <div className="flex items-center gap-3">
          <span className="text-3xl leading-none select-none opacity-40">文</span>
          <div>
            <h1 className="font-serif text-xl font-semibold text-ink-accent tracking-wider leading-tight">文脉</h1>
            <p className="text-[10px] text-ink-text-muted font-sans tracking-[0.15em]">WENMAI STUDIO</p>
          </div>
        </div>
      </div>

      {/* Novel Switcher */}
      <div className="px-4 py-3 border-b border-ink-border">
        <select
          value={activeNovel}
          onChange={(e) => {
            const name = e.target.value;
            if (!name) return;
            switchNovel(name).then(() => {
              setActiveNovel(name);
              loadNovels();
              navigate("/");
            });
          }}
          className="w-full bg-ink-card border border-ink-border text-ink-text text-sm rounded-lg px-3 py-2 font-sans focus:outline-none focus:border-ink-accent transition-colors cursor-pointer"
        >
          {novels.length === 0 && <option>加载中...</option>}
          {novels.map((n) => (
            <option key={n.name} value={n.name}>
              {n.name} {n.is_active ? "✓" : ""}
            </option>
          ))}
        </select>
        <div className="flex gap-4 mt-2 text-xs text-ink-text-muted font-sans">
          {novels.find((n) => n.is_active) && (
            <>
              <span>
                {novels.find((n) => n.is_active)?.chapter_count || 0} 章
              </span>
              <span>
                {novels.find((n) => n.is_active)?.entity_count || 0} 实体
              </span>
            </>
          )}
        </div>
      </div>

      {/* Nav Links */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-sans transition-all duration-200 relative group ${
                isActive
                  ? "bg-ink-accent/10 text-ink-accent font-medium before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2 before:w-[3px] before:h-5 before:rounded-r-full before:bg-ink-accent"
                  : "text-ink-text-secondary hover:text-ink-text hover:bg-ink-card"
              }`
            }
          >
            <span className="text-base w-5 text-center">{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-4 py-4 border-t border-ink-border text-xs text-ink-text-muted font-sans">
        文脉工作室 v1.0
      </div>
    </aside>
  );
}
