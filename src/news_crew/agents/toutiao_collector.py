"""
今日头条热榜采集员 Agent
外倾性高，对热点敏感，擅长识别情绪化内容。
使用搜索工具 + 网页抓取工具。
"""
from crewai import Agent
from ..tools.search_tool import web_search
from ..tools.scrape_tool import web_scrape


def create_toutiao_collector(llm, cfg: dict) -> Agent:
    """创建今日头条热榜采集员（配置来自 config/agents/toutiao_collector.yaml）"""
    return Agent(
        role=cfg["role"],
        goal=cfg["goal"],
        backstory=cfg["backstory"],
        tools=[web_search, web_scrape],
        llm=llm,
        allow_delegation=cfg.get("allow_delegation", False),
        verbose=cfg.get("verbose", True),
    )
