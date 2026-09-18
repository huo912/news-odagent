"""
知乎热榜采集员 Agent
开放性高，关注深度讨论，擅长识别争议性话题。
使用搜索工具。
"""
from crewai import Agent
from ..tools.search_tool import web_search


def create_zhihu_collector(llm, cfg: dict) -> Agent:
    """创建知乎热榜采集员（配置来自 config/agents/zhihu_collector.yaml）"""
    return Agent(
        role=cfg["role"],
        goal=cfg["goal"],
        backstory=cfg["backstory"],
        tools=[web_search],
        llm=llm,
        allow_delegation=cfg.get("allow_delegation", False),
        verbose=cfg.get("verbose", True),
    )
