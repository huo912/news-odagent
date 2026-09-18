"""
舆情评论分析员 Agent
宜人性高，善于理解多方观点，客观中立。
使用搜索工具（搜评论）+ 情感分析。
"""
from crewai import Agent
from ..tools.search_tool import web_search
from ..tools.sentiment_tool import sentiment_analyze


def create_comment_analyst(llm, cfg: dict) -> Agent:
    """创建舆情评论分析员（配置来自 config/agents/comment_analyst.yaml）"""
    return Agent(
        role=cfg["role"],
        goal=cfg["goal"],
        backstory=cfg["backstory"],
        tools=[web_search, sentiment_analyze],
        llm=llm,
        allow_delegation=cfg.get("allow_delegation", False),
        verbose=cfg.get("verbose", True),
    )
