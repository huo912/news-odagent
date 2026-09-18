"""
跨平台内容整合员 Agent
尽责性极高，擅长数据清洗和标准化。
使用 Python 代码执行工具。
"""
from crewai import Agent
from ..tools.python_tool import python_execute


def create_content_integrator(llm, cfg: dict) -> Agent:
    """创建跨平台内容整合员（配置来自 config/agents/content_integrator.yaml）"""
    return Agent(
        role=cfg["role"],
        goal=cfg["goal"],
        backstory=cfg["backstory"],
        tools=[python_execute],
        llm=llm,
        allow_delegation=cfg.get("allow_delegation", False),
        verbose=cfg.get("verbose", True),
    )
