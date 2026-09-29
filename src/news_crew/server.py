"""
kagent A2A 服务入口（方案 A：BYO Harness 部署）

把 build_crew() 构建的 Crew 包装成 kagent 兼容的 A2A 服务：
    KAgentApp(crew=..., agent_card=...) -> FastAPI -> uvicorn

每收到一条 A2A 消息，CrewAIAgentExecutor 会 kickoff 一次完整流水线。
本项目的任务描述中没有 {input} 占位符，因此 A2A 消息文本不参与任务
输入，每条消息等价于触发一次完整的新闻采集/处理/推送流程。

兼容性说明（kagent-crewai 0.10.2 + crewai 1.15.22）：
    1. crewai 1.15.22 移除了 crewai.memory.LongTermMemory，而
       kagent-crewai 在模块顶层 import 它（仅在 crew.memory=True 时
       才会真正使用）。本项目 memory=False，因此在 import 前注入
       一个轻量 shim。
    2. kagent-core 读取的环境变量是 KAGENT_URL（不是 KAGENT_API_URL），
       与 KAGENT_NAME / KAGENT_NAMESPACE 一起在 import 时求值。

本地开发（无需手动 export，下方 _ensure_kagent_env 会补默认值）：
    python -X utf8 -m news_crew.server

部署到 k8s（kagent BYO Harness）时，KAGENT_URL / KAGENT_NAME /
KAGENT_NAMESPACE 由 Harness 编译器自动注入，无需手动配置。

环境变量：
    HOST   监听地址，默认 0.0.0.0
    PORT   监听端口，默认 8080
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# agent-card.json 与本文件同目录
CARD_PATH = Path(__file__).resolve().parent / "agent-card.json"


def _ensure_kagent_env() -> None:
    """为 kagent-core 补齐必需环境变量的本地默认值。

    kagent-core 在 import 时（模块级）读取 KAGENT_URL / KAGENT_NAME /
    KAGENT_NAMESPACE，缺一即抛 ValueError。本地开发不设置也能启动；
    k8s 部署时 Harness 编译器注入的真实值优先（setdefault 不覆盖）。
    """
    defaults = {
        "KAGENT_URL": "http://localhost:8083",
        "KAGENT_NAME": "news-odagent",
        "KAGENT_NAMESPACE": "default",
    }
    for key, value in defaults.items():
        if not os.getenv(key):
            os.environ[key] = value
            logger.info("kagent 环境变量未设置，使用默认值 %s=%s", key, value)


def _patch_crewai_memory() -> None:
    """为 crewai >= 1.15 注入 LongTermMemory 兼容 shim。

    kagent-crewai 0.10.2 在模块顶层 `from crewai.memory import
    LongTermMemory`，但该类在 crewai 1.15.22 的 memory 重构中被移除。
    它只在 crew.memory=True 时被实例化，本项目 memory=False，注入
    一个占位类即可满足 import。
    """
    import crewai.memory as _cm

    if not hasattr(_cm, "LongTermMemory"):
        class _LongTermMemoryShim:
            def __init__(self, storage=None):
                self.storage = storage

        _cm.LongTermMemory = _LongTermMemoryShim


def create_app():
    """构建 kagent FastAPI 应用（供测试/嵌入复用）。"""
    _ensure_kagent_env()
    _patch_crewai_memory()
    from kagent.crewai import KAgentApp

    from .crew import build_crew
    from .nacos_registry import register_service

    # k8s 中由 NACOS_REGISTER_ENABLED=0 禁用注册（Pod IP 无意义），
    # 本地运行时保持注册行为
    register_service()

    with open(CARD_PATH, "r", encoding="utf-8") as f:
        agent_card = json.load(f)

    app = KAgentApp(crew=build_crew(), agent_card=agent_card)
    return app.build()


def main() -> None:
    """启动 kagent A2A 服务。"""
    import uvicorn

    logging.basicConfig(level=logging.INFO)

    server = create_app()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    logger.info("启动 kagent A2A 服务: %s:%s", host, port)
    uvicorn.run(server, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
