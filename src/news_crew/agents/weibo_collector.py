"""
微博热搜采集员 Agent
外倾性高，对热点敏感，擅长识别情绪化内容。
使用搜索工具。
"""
from crewai import Agent
from ..tools.search_tool import web_search


def create_weibo_collector(llm, cfg: dict) -> Agent:
    """创建微博热搜采集员（配置来自 config/agents/weibo_collector.yaml）"""
    return Agent(
        role=cfg["role"],
        goal=cfg["goal"],
        backstory=cfg["backstory"],
        tools=[web_search],
        llm=llm,
        allow_delegation=cfg.get("allow_delegation", False),
        verbose=cfg.get("verbose", True),
        max_iter=cfg.get("max_iter", 25),
    )
