"""
动态 LLM 包装器（运行中热切换模型）

CrewAI 的 Agent 在构建时通过 get_llm() 创建一次 LLM 实例，之后整个运行期间
固定使用该实例，导致 Nacos 远程修改模型后，已运行的 agent 不会切换模型。

本模块提供 DynamicLLM：它继承 crewai 的 BaseLLM（可通过 Agent 的 Pydantic
校验），内部持有一个真正的 LLM 委托实例，并在每次 call()/acall() 前从 Nacos
读取最新配置、必要时重建委托实例。这样 agent 每次调用 LLM 都会使用 Nacos 上
最新的模型，实现真正的运行中热更新。

不影响现有功能：
- 未配置 Nacos（NACOS_SERVER_ADDR 为空）时，get_llm_overrides() 返回空 dict，
  行为与原来完全一致（agent YAML llm 段 > .env 环境变量）。
- call/acall/supports_stop_words/supports_function_calling 等均委托给底层 LLM，
  对 CrewAI 完全透明。
"""
from __future__ import annotations

import os
from typing import Any

from crewai import LLM
from crewai.llms.base_llm import BaseLLM
from pydantic import PrivateAttr


def _resolve_config(agent_key: str | None, agent_cfg: dict) -> dict:
    """合并 Nacos / YAML / .env 配置，返回当前生效的 LLM 配置。"""
    from .nacos_config import get_llm_overrides

    # 远程覆盖项（Nacos 未配置时为空 dict）
    overrides = get_llm_overrides(agent_key)
    local_llm = (agent_cfg or {}).get("llm") or {}
    cfg = {**local_llm, **overrides}
    api_key = cfg.get("api_key") or os.getenv("OPENAI_API_KEY")
    model = cfg.get("model") or os.getenv("OPENAI_MODEL_NAME", "gpt-4o")
    base_url = cfg.get("base_url") or os.getenv("OPENAI_API_BASE")
    # 推理强度（low/medium/high）：qwen3.8-27b 是推理模型，
    # 默认强度下大 prompt 思考时间可能超过客户端读超时导致
    # "Failed to connect to OpenAI API: Request timed out"。
    # 实测 low 可将大 prompt 响应从 ~59s 降到 ~27s。
    reasoning_effort = cfg.get("reasoning_effort") or os.getenv(
        "OPENAI_REASONING_EFFORT", "low"
    )
    timeout = cfg.get("timeout") or os.getenv("OPENAI_TIMEOUT")

    resolved: dict[str, Any] = {"model": model}
    if api_key:
        resolved["api_key"] = api_key
    if base_url:
        resolved["base_url"] = base_url
    if reasoning_effort:
        resolved["reasoning_effort"] = reasoning_effort
    if timeout:
        resolved["timeout"] = float(timeout)
    return resolved


def _build_llm(agent_key: str | None, agent_cfg: dict) -> LLM:
    """根据当前 Nacos 配置构造底层 LLM。"""
    return LLM(**_resolve_config(agent_key, agent_cfg))


class DynamicLLM(BaseLLM):
    """
    每次调用前从 Nacos 刷新模型配置的 LLM 包装器。

    用法与 LLM 相同，可传给 Agent 的 llm 参数。
    """

    # 委托实例与构造参数保存为私有属性，避免与 BaseLLM 的 Pydantic 字段冲突
    _llm: LLM = PrivateAttr(default=None)
    _agent_key: str | None = PrivateAttr(default=None)
    _agent_cfg: dict = PrivateAttr(default_factory=dict)
    _last_cfg: dict = PrivateAttr(default_factory=dict)

    def __init__(
        self,
        agent_key: str | None = None,
        agent_cfg: dict | None = None,
        **data: Any,
    ) -> None:
        # 首次解析配置并构造底层 LLM（同时完成首次 Nacos 拉取）
        cfg = _resolve_config(agent_key, agent_cfg or {})
        llm = LLM(**cfg)
        # 用底层 LLM 的关键配置初始化 BaseLLM 的 Pydantic 字段
        super().__init__(
            model=llm.model,
            base_url=llm.base_url,
            api_key=llm.api_key,
            **data,
        )
        self._llm = llm
        self._last_cfg = cfg
        self._agent_key = agent_key
        self._agent_cfg = agent_cfg or {}

    def _refresh(self) -> LLM:
        """返回最新配置对应的底层 LLM（配置未变化时复用，避免重复构造）。"""
        cfg = _resolve_config(self._agent_key, self._agent_cfg)
        # 模型配置变化时才替换实例；否则复用，保持 callbacks 等状态
        if cfg != self._last_cfg:
            self._last_cfg = cfg
            self._llm = LLM(**cfg)
            # 同步 BaseLLM 字段，供 CrewAI 内部读取
            self.model = cfg["model"]
            self.base_url = cfg.get("base_url")
            self.api_key = cfg.get("api_key")
        return self._llm

    # ---- 委托给底层 LLM ----
    def call(self, *args: Any, **kwargs: Any) -> Any:
        return self._refresh().call(*args, **kwargs)

    async def acall(self, *args: Any, **kwargs: Any) -> Any:
        return await self._refresh().acall(*args, **kwargs)

    def supports_stop_words(self) -> bool:
        return self._llm.supports_stop_words()

    def supports_function_calling(self) -> bool:
        return self._llm.supports_function_calling()

    def get_context_window_size(self) -> int:
        return self._llm.get_context_window_size()
