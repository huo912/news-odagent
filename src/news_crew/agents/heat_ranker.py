"""
全平台热度排序员 Agent
神经质高，对数据异常敏感，严谨排序。
使用 Python 代码执行 + 计算工具。
"""
from crewai import Agent
from ..tools.python_tool import python_execute


def create_heat_ranker(llm, cfg: dict) -> Agent:
    """创建全平台热度排序员（配置来自 config/agents/heat_ranker.yaml）"""
    return Agent(
        role=cfg["role"],
        goal=cfg["goal"],
        backstory=cfg["backstory"],
        tools=[python_execute],
        llm=llm,
        allow_delegation=cfg.get("allow_delegation", False),
        verbose=cfg.get("verbose", True),
    )
