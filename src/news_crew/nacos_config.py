"""
Nacos 远程模型配置（热更新）

通过 Nacos 配置中心远程指定 agent 使用的模型。
未配置 NACOS_SERVER_ADDR 时返回空覆盖项，调用方回退到现有配置
（agent YAML 的 llm 段 > .env 环境变量）。

Nacos 配置内容支持 YAML 或 JSON，结构：

    # 全局默认（作用于所有 agent）
    default:
      model: qwen3.8-27b
      # api_key: sk-xxx
      # base_url: http://10.105.1.5:4001/v1
      # reasoning_effort: low
      # timeout: 600
    # 按 agent 覆盖（key 为 agent 名，如 toutiao_collector）
    agents:
      toutiao_collector:
        model: gpt-4o

优先级（高 -> 低）：
    Nacos agents.<agent> > Nacos default > agent YAML llm 段 > .env 环境变量

热更新（运行中热切换）：
    首次拉取后，后台线程持续监听 Nacos 配置变更。远程修改配置后，
    进程内缓存自动刷新；agent 通过 DynamicLLM（dynamic_llm.py）在每次
    调用 LLM 前读取最新缓存，运行中的 agent 也会立即切换到新模型，
    无需重启进程。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

# 环境变量
ENV_SERVER_ADDR = "NACOS_SERVER_ADDR"  # 如 "127.0.0.1:8848"，多个地址用逗号分隔
ENV_NAMESPACE = "NACOS_NAMESPACE"      # 命名空间 ID，默认 public（留空）
ENV_GROUP = "NACOS_GROUP"              # 配置分组，默认 DEFAULT_GROUP
ENV_DATA_ID = "NACOS_DATA_ID"          # 配置 dataId，默认 news-crew-llm.yaml
ENV_USERNAME = "NACOS_USERNAME"        # 访问用户名（可选）
ENV_PASSWORD = "NACOS_PASSWORD"        # 访问密码（可选）
ENV_TIMEOUT_MS = "NACOS_TIMEOUT_MS"    # Nacos 请求超时（毫秒），默认 10000

DEFAULT_DATA_ID = "news-crew-llm.yaml"
DEFAULT_GROUP = "DEFAULT_GROUP"
DEFAULT_TIMEOUT_MS = 10_000

_lock = threading.Lock()
_config: dict | None = None
_worker_started = False
_ready = threading.Event()  # 首次拉取完成（成功或失败）后置位


def _parse_content(content: str) -> dict:
    """解析 Nacos 配置内容（YAML 或 JSON），返回 dict"""
    if not content or not content.strip():
        return {}
    text = content.strip()
    if text.startswith("{"):
        data = json.loads(text)
    else:
        import yaml

        data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Nacos 配置内容必须是映射（dict）结构")
    return data


def _apply_config(content: str | None) -> None:
    """解析配置内容并更新进程内缓存（解析失败时保留旧配置）"""
    global _config
    try:
        cfg = _parse_content(content or "")
    except Exception as e:  # noqa: BLE001 - 坏配置不覆盖已有可用配置
        logger.warning("Nacos 配置解析失败，保留当前配置: %s", e)
        return
    with _lock:
        _config = cfg
    logger.info("Nacos 远程模型配置已更新: %s", cfg or "(空)")


def _run_worker() -> None:
    """
    后台 worker：独立事件循环中运行 nacos-sdk-python >= 3.x（全异步 API）。

    1. 首次拉取配置（阻塞等待，带超时，失败不阻断启动）
    2. 注册变更监听，远程修改配置后自动刷新缓存（热更新）
    """
    from v2.nacos import (  # type: ignore[import-not-found]
        ClientConfigBuilder,
        ConfigParam,
        NacosConfigService,
    )

    namespace = os.getenv(ENV_NAMESPACE, "").strip()
    username = os.getenv(ENV_USERNAME, "").strip()
    password = os.getenv(ENV_PASSWORD, "").strip()
    data_id = os.getenv(ENV_DATA_ID, DEFAULT_DATA_ID).strip()
    group = os.getenv(ENV_GROUP, DEFAULT_GROUP).strip()
    timeout_ms = int(os.getenv(ENV_TIMEOUT_MS, DEFAULT_TIMEOUT_MS))

    async def _on_change(tenant: str, grp: str, did: str, content: str) -> None:
        # SDK 在配置变更时回调（async），刷新缓存实现热更新
        _apply_config(content)

    async def _main() -> None:
        builder = ClientConfigBuilder().server_address(
            os.getenv(ENV_SERVER_ADDR, "").strip()
        ).timeout_ms(timeout_ms)
        if namespace:
            builder = builder.namespace_id(namespace)
        if username:
            builder = builder.username(username).password(password)
        service = NacosConfigService(builder.build())
        try:
            content = await service.get_config(ConfigParam(data_id=data_id, group=group))
            _apply_config(content)
            # 首次拉取完成，立即置位就绪事件，唤醒 get_remote_llm_config 的等待。
            # 注意：不能只依赖外层 finally —— 监听阶段 worker 常驻不退出，
            # finally 永远不会执行，会导致每次读缓存都白等 15s 超时。
            _ready.set()
            await service.add_listener(data_id, group, _on_change)
            logger.info(
                "Nacos 配置监听已启动: %s/%s（远程变更将热更新）", group, data_id
            )
            # 常驻：保持事件循环存活以接收变更推送
            await asyncio.Event().wait()
        finally:
            _ready.set()  # 异常退出时也要置位，避免调用方永久等待
            await service.shutdown()

    try:
        asyncio.run(_main())
    except Exception as e:  # noqa: BLE001 - 后台线程异常不向主线程传播
        logger.warning("Nacos 配置 worker 退出，回退到本地配置: %s", e)
    finally:
        _ready.set()


def _ensure_worker() -> None:
    """确保后台 worker 已启动（幂等）；未配置 NACOS_SERVER_ADDR 时直接标记就绪"""
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    if not os.getenv(ENV_SERVER_ADDR, "").strip():
        _ready.set()
        return
    try:
        # 探测 SDK 是否可用；不可用则不启动 worker（回退本地配置）
        import v2.nacos  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        logger.warning(
            "已配置 NACOS_SERVER_ADDR 但未安装 nacos-sdk-python，"
            "回退到本地配置。安装: pip install -e \".[nacos]\""
        )
        _ready.set()
        return
    t = threading.Thread(target=_run_worker, name="nacos-config-worker", daemon=True)
    t.start()


def get_remote_llm_config() -> dict | None:
    """
    获取远程模型配置（进程内缓存，后台 worker 持续热更新）。

    返回结构：
        {"default": {...}, "agents": {agent_key: {...}}}

    首次调用会等待后台 worker 完成首次拉取（带超时）；
    未配置 Nacos 或拉取失败时返回 None（回退到现有本地配置）。
    """
    _ensure_worker()
    # 等待首次拉取完成（成功或失败）；worker 异常退出时 _ready 也会置位
    _ready.wait(timeout=15)
    with _lock:
        return _config


def get_llm_overrides(agent_key: str | None = None) -> dict:
    """
    获取对指定 agent 生效的远程模型覆盖项。

    合并规则：Nacos agents.<agent_key> 覆盖 Nacos default。
    未启用 Nacos 时返回空 dict。
    """
    remote = get_remote_llm_config()
    if not remote:
        return {}
    merged = dict(remote.get("default") or {})
    if agent_key:
        merged.update((remote.get("agents") or {}).get(agent_key) or {})
    return merged
