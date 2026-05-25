"""LLM 调用层 —— 支持多种后端 + Agent 循环（function calling）。"""

import json
import os
import re
import time
from typing import Optional

import certifi
import httpx
import yaml


def _retry_api_call(fn, max_retries: int = 3, base_delay: float = 2.0):
    """带指数退避的 API 调用重试，处理 429/5xx 等临时故障。"""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except httpx.HTTPStatusError as e:
            last_exc = e
            status = e.response.status_code
            if status in (429, 500, 502, 503, 504) and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                import sys
                print(f"  [RETRY] HTTP {status}，{delay:.0f}s 后重试 ({attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(delay)
                continue
            raise
    raise last_exc


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
        tool_choice: str | None = "auto",
    ) -> dict:
        """单次 LLM 调用，返回完整 response object。"""
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        # DeepSeek thinking 模型需要显式关闭推理模式，否则要求回传 reasoning_content
        if self.provider == "deepseek":
            body["thinking"] = {"type": "disabled"}
        # DeepSeek 在 tool calling 模式下不支持 temperature 参数
        if not tools:
            body["temperature"] = self.temperature
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if tools:
            body["tools"] = tools
            # DeepSeek 需要显式传入 tool_choice，但不能是 None
            if tool_choice:
                body["tool_choice"] = tool_choice

        def _call():
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
            if resp.status_code >= 400:
                import sys
                print(f"\n[LLM ERROR] HTTP {resp.status_code}", file=sys.stderr)
                print(f"  Request body keys: {list(body.keys())}", file=sys.stderr)
                try:
                    err_body = resp.text[:1000]
                    print(f"  Response: {err_body}", file=sys.stderr)
                except Exception:
                    pass
            resp.raise_for_status()
            return resp.json()

        return _retry_api_call(_call)

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
                # 将 assistant 消息加入历史（保留 reasoning_content 以兼容 DeepSeek thinking 模型）
                assistant_msg = {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": message["tool_calls"],
                }
                if message.get("reasoning_content"):
                    assistant_msg["reasoning_content"] = message["reasoning_content"]
                messages.append(assistant_msg)

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
        self, system: str, user: str, json_mode: bool = False,
        temperature: float | None = None,
    ) -> str:
        """OpenAI 兼容 API（无 tools）。"""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        def _call():
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

        return _retry_api_call(_call)

    def _call_anthropic(self, system: str, user: str) -> str:
        """Anthropic Messages API。"""
        def _call():
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

        return _retry_api_call(_call)

    def generate(self, system: str, user: str, json_mode: bool = False,
                 temperature: float | None = None) -> str:
        """统一接口（无 tools）。"""
        if self.provider == "anthropic":
            return self._call_anthropic(system, user)
        else:
            return self._call_openai_compatible(system, user, json_mode, temperature)

    # ── 蒸馏三阶段 ────────────────────────────────────────────

    def observe(self, chapter_text: str, known_entities: list[str],
                current_states: str) -> str:
        """Observer: 自由文本观察，过度提取事实变化。temp 0.6"""
        from prompts.observer import OBSERVER_SYSTEM, OBSERVER_USER
        prompt = OBSERVER_USER.format(
            chapter_text=chapter_text,
            current_states=current_states[:5000],
        )
        known = ", ".join(known_entities[:50])
        system = OBSERVER_SYSTEM.replace("{known_entities}", known)
        return self.generate(system, prompt, json_mode=False, temperature=0.6)

    def settle(self, observations: str, known_entities: list[str],
               current_states: str, retry_hint: str = "") -> dict:
        """Settler: 将观察转化为 JSON delta。temp 0.25"""
        from prompts.settler import SETTLER_SYSTEM, SETTLER_USER
        known = ", ".join(known_entities[:50])
        system = SETTLER_SYSTEM

        user_text = SETTLER_USER.format(
            observations=observations,
            current_states=current_states[:5000],
            known_entities=known,
        )
        if retry_hint:
            user_text += f"\n\n{retry_hint}"

        raw = self.generate(system, user_text, json_mode=True, temperature=0.25)
        return self._parse_json(raw)

    def validate_state(self, chapter_text: str, observations: str,
                       old_state: str, new_state: str) -> dict:
        """State Validator: 比较新旧状态，检测矛盾。temp 0.15"""
        from prompts.state_validator import VALIDATOR_SYSTEM, VALIDATOR_USER
        prompt = VALIDATOR_USER.format(
            chapter_summary=chapter_text[:2000],
            observations=observations[:3000],
            old_state=old_state[:5000],
            new_state=new_state[:5000],
        )
        raw = self.generate(VALIDATOR_SYSTEM, prompt, json_mode=True, temperature=0.15)
        return self._parse_json(raw)

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

    def generate_outline(self, context: str, **kwargs) -> str:
        """生成篇章大纲。kwargs 用于格式化 OUTLINE_SYSTEM 中的占位符。"""
        from prompts.generate_outline import OUTLINE_SYSTEM
        system = OUTLINE_SYSTEM.format(**kwargs) if kwargs else OUTLINE_SYSTEM
        return self.generate(system, context)

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
