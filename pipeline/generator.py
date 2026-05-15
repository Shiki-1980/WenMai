"""LLM 调用层 —— 支持多种后端。"""

import json
import os
import re
from typing import Optional

import httpx
import yaml


class LLMGenerator:
    def __init__(self, config_path: str):
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        self.llm_cfg = cfg["llm"]
        self.provider = self.llm_cfg["provider"]
        self.model = self.llm_cfg["model"]
        self.temperature = self.llm_cfg.get("temperature", 0.8)
        self.max_tokens = self.llm_cfg.get("max_tokens", 16384)

        api_key = self.llm_cfg["api_key"]
        if api_key.startswith("${") and api_key.endswith("}"):
            env_var = api_key[2:-1]
            api_key = os.environ.get(env_var, "")
        self.api_key = api_key
        self.api_base = self.llm_cfg.get(
            "api_base", "https://api.deepseek.com"
        )

    def _call_openai_compatible(
        self, system: str, user: str, json_mode: bool = False
    ) -> str:
        """OpenAI 兼容 API（DeepSeek、Qwen 等）。"""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        resp = httpx.post(
            f"{self.api_base}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _call_anthropic(self, system: str, user: str) -> str:
        """Anthropic Messages API。"""
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]

    def generate(self, system: str, user: str, json_mode: bool = False) -> str:
        """统一接口。"""
        if self.provider == "anthropic":
            return self._call_anthropic(system, user)
        else:
            return self._call_openai_compatible(system, user, json_mode)

    def generate_chapter(self, context: str) -> str:
        """生成章节正文。"""
        from prompts.generate_chapter import CHAPTER_SYSTEM

        return self.generate(CHAPTER_SYSTEM, context)

    def distill_chapter(self, chapter_text: str, known_entities: str) -> dict:
        """蒸馏章节，返回结构化 JSON。"""
        from prompts.distill import DISTILL_SYSTEM, DISTILL_USER

        prompt = DISTILL_USER.format(
            chapter_text=chapter_text,
            known_entities=known_entities,
            max_chars=300,
        )
        raw = self.generate(DISTILL_SYSTEM, prompt, json_mode=True)
        return self._parse_json(raw)

    def generate_outline(self, context: str) -> str:
        """生成篇章大纲。"""
        from prompts.generate_outline import OUTLINE_SYSTEM

        return self.generate(OUTLINE_SYSTEM, context)

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """尽力从 LLM 输出中提取 JSON。"""
        raw = raw.strip()
        # 去掉可能的 markdown 代码块
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            raw = m.group(0)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
