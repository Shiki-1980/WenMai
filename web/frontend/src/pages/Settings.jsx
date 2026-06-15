import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { getConfig, updateLLMConfig } from "../lib/api";

const LLM_PROVIDERS = [
  { value: "deepseek", label: "DeepSeek" },
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "openai-compatible", label: "OpenAI 兼容" },
];

function Field({ label, hint, children }) {
  return (
    <div className="mb-5">
      <label className="block text-sm font-sans text-ink-text-secondary mb-1.5">
        {label}
      </label>
      {children}
      {hint && (
        <p className="text-xs text-ink-text-muted mt-1 font-sans">{hint}</p>
      )}
    </div>
  );
}

export default function Settings() {
  const [config, setConfig] = useState(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showKey, setShowKey] = useState(false);

  useEffect(() => {
    getConfig()
      .then(setConfig)
      .catch(() => setMessage({ type: "error", text: "无法加载配置" }))
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setMessage(null);
    try {
      await updateLLMConfig({
        provider: config.provider,
        model: config.model,
        api_base: config.api_base,
        api_key: config.api_key,
        max_tokens: config.max_tokens,
        temperature: config.temperature,
        chapter_words: config.chapter_words,
      });
      setMessage({ type: "success", text: "配置已保存" });
    } catch (e) {
      setMessage({ type: "error", text: e.message || "保存失败" });
    } finally {
      setSaving(false);
    }
  };

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

  return (
    <div className="max-w-2xl mx-auto">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
      >
        <h2 className="font-serif text-3xl font-semibold text-ink-text mb-1">
          LLM 设置
        </h2>
        <p className="text-sm text-ink-text-muted font-sans mb-8">
          配置大语言模型的连接参数，修改后即时生效
        </p>
      </motion.div>

      {config && (
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.1 }}
          className="bg-ink-card border border-ink-border rounded-xl p-6 mb-6"
        >
          {/* Provider */}
          <Field label="服务商" hint="选择 LLM API 服务商">
            <select
              value={config.provider}
              onChange={(e) => setConfig({ ...config, provider: e.target.value })}
              className="w-full bg-ink-surface border border-ink-border text-ink-text text-sm rounded-lg px-3 py-2.5 font-sans focus:outline-none focus:border-ink-accent transition-colors"
            >
              {LLM_PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </Field>

          {/* Model */}
          <Field label="模型" hint="模型名称，需要与 API 服务商兼容">
            <input
              type="text"
              value={config.model}
              onChange={(e) => setConfig({ ...config, model: e.target.value })}
              className="w-full bg-ink-surface border border-ink-border text-ink-text text-sm rounded-lg px-3 py-2.5 font-mono focus:outline-none focus:border-ink-accent transition-colors"
            />
          </Field>

          {/* API Base */}
          <Field label="API 地址" hint="API 端点地址，使用 OpenAI 兼容格式的路径">
            <input
              type="text"
              value={config.api_base}
              onChange={(e) => setConfig({ ...config, api_base: e.target.value })}
              className="w-full bg-ink-surface border border-ink-border text-ink-text text-sm rounded-lg px-3 py-2.5 font-mono focus:outline-none focus:border-ink-accent transition-colors"
              placeholder="https://api.deepseek.com"
            />
          </Field>

          {/* API Key */}
          <Field label="API Key" hint="用于认证的 API 密钥，请妥善保管">
            <div className="relative">
              <input
                type={showKey ? "text" : "password"}
                value={config.api_key}
                onChange={(e) => setConfig({ ...config, api_key: e.target.value })}
                className="w-full bg-ink-surface border border-ink-border text-ink-text text-sm rounded-lg px-3 py-2.5 pr-12 font-mono focus:outline-none focus:border-ink-accent transition-colors"
                placeholder="sk-..."
              />
              <button
                type="button"
                onClick={() => setShowKey(!showKey)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-text-muted hover:text-ink-text transition-colors text-xs font-sans"
              >
                {showKey ? "隐藏" : "显示"}
              </button>
            </div>
          </Field>

          {/* Max Tokens */}
          <Field label="Max Tokens" hint="单次生成的最大 token 数">
            <input
              type="number"
              value={config.max_tokens}
              onChange={(e) =>
                setConfig({ ...config, max_tokens: parseInt(e.target.value) || 0 })
              }
              className="w-full bg-ink-surface border border-ink-border text-ink-text text-sm rounded-lg px-3 py-2.5 font-mono focus:outline-none focus:border-ink-accent transition-colors"
            />
          </Field>

          {/* Temperature */}
          <Field label="Temperature" hint="生成随机度 (0-2)，越低越确定，越高越有创意">
            <input
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={config.temperature}
              onChange={(e) =>
                setConfig({ ...config, temperature: parseFloat(e.target.value) || 0 })
              }
              className="w-full bg-ink-surface border border-ink-border text-ink-text text-sm rounded-lg px-3 py-2.5 font-mono focus:outline-none focus:border-ink-accent transition-colors"
            />
          </Field>

          {/* Chapter Words */}
          <Field label="章节字数" hint="生成每章时的目标字数">
            <input
              type="number"
              value={config.chapter_words}
              onChange={(e) =>
                setConfig({ ...config, chapter_words: parseInt(e.target.value) || 0 })
              }
              className="w-full bg-ink-surface border border-ink-border text-ink-text text-sm rounded-lg px-3 py-2.5 font-mono focus:outline-none focus:border-ink-accent transition-colors"
            />
          </Field>
        </motion.div>
      )}

      {/* Actions */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
        className="flex items-center gap-4"
      >
        <button
          onClick={handleSave}
          disabled={saving}
          className="bg-ink-accent text-ink-bg font-sans font-medium text-sm px-6 py-2.5 rounded-lg hover:bg-ink-accent-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? "保存中..." : "保存配置"}
        </button>

        {message && (
          <motion.span
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className={`text-sm font-sans ${
              message.type === "success" ? "text-ink-success" : "text-ink-error"
            }`}
          >
            {message.text}
          </motion.span>
        )}
      </motion.div>

      {/* Current config info */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.3 }}
        className="mt-8 p-5 bg-ink-surface border border-ink-border rounded-xl"
      >
        <h3 className="font-serif text-lg text-ink-text mb-2">当前项目</h3>
        <p className="text-sm text-ink-text-secondary font-sans">
          Vault 路径: {config?.vault_path}
        </p>
        <p className="text-sm text-ink-text-secondary font-sans">
          当前小说: {config?.active_novel || "未选择"}
        </p>
        <p className="text-sm text-ink-text-secondary font-sans">
          章节字数: {config?.chapter_words || 4000} 字
        </p>
      </motion.div>
    </div>
  );
}
