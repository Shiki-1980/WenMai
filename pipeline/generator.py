"""LLM 调用层 —— 支持多种后端 + Agent 循环（function calling）。"""

import json
import os
import re
from typing import Optional

import certifi
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

    # ── 底层 API 调用 ──────────────────────────────────────────

    def _chat_once(
        self,
        messages: list[dict],
        json_mode: bool = False,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> dict:
        """单次 LLM 调用，返回完整 response object。"""
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice

        resp = httpx.post(
            f"{self.api_base}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=300,
            verify=certifi.where(),
        )
        resp.raise_for_status()
        return resp.json()

    # ── Agent 循环：写作 + 按需工具调用 ─────────────────────────

    def generate_chapter_with_tools(
        self,
        chapter_context: str,
        tool_executor,
        max_tool_rounds: int = 8,
    ) -> str:
        """Agent 循环写一章。

        LLM 写作过程中可以调用 lookup_entity / lookup_recent_events /
        check_world_rules 等工具来检索需要的信息。

        Args:
            chapter_context: 写作上下文（大纲、基本参数）
            tool_executor: ToolExecutor 实例
            max_tool_rounds: 最大工具调用轮次（防止无限循环）

        Returns:
            章节正文
        """
        from prompts.generate_chapter import CHAPTER_SYSTEM
        from tools import WRITER_TOOLS

        messages = [
            {"role": "system", "content": CHAPTER_SYSTEM},
            {"role": "user", "content": chapter_context},
        ]

        for _round in range(max_tool_rounds):
            response = self._chat_once(
                messages,
                json_mode=False,
                tools=WRITER_TOOLS,
                tool_choice="auto",
            )
            choice = response["choices"][0]
            message = choice["message"]

            # 如果 LLM 决定写正文（不调工具）
            if message.get("content") and not message.get("tool_calls"):
                return message["content"]

            # 如果 LLM 调用了工具
            if message.get("tool_calls"):
                # 将 assistant 消息加入历史
                messages.append({
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": message["tool_calls"],
                })

                # 执行每个工具调用
                for tc in message["tool_calls"]:
                    func_name = tc["function"]["name"]
                    func_args = json.loads(tc["function"]["arguments"])
                    result = tool_executor.execute(func_name, func_args)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })

                continue

            # 如果没有 content 也没有 tool_calls（空响应）→ 重试
            if not message.get("content"):
                continue

            return message["content"]

        # 超过最大轮次，强制要求输出
        messages.append({
            "role": "user",
            "content": "请基于以上信息，直接写出本章正文，不要调用更多工具。",
        })
        response = self._chat_once(messages, json_mode=False)
        return response["choices"][0]["message"].get("content", "")

    # ── 简单调用（蒸馏/大纲/非 Agent 场景）─────────────────────

    def _call_openai_compatible(
        self, system: str, user: str, json_mode: bool = False
    ) -> str:
        """OpenAI 兼容 API（无 tools）。"""
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
            verify=certifi.where(),
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
            verify=certifi.where(),
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]

    def generate(self, system: str, user: str, json_mode: bool = False) -> str:
        """统一接口（无 tools）。"""
        if self.provider == "anthropic":
            return self._call_anthropic(system, user)
        else:
            return self._call_openai_compatible(system, user, json_mode)

    # ── 保留旧 API 兼容 ────────────────────────────────────────

    def generate_chapter(self, context: str) -> str:
        """旧接口：生成章节正文（无 Agent 循环）。"""
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
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            raw = m.group(0)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
