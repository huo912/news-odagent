"""
新闻推送专员 Agent
外倾性高，擅长简洁表达，注重可读性。
使用文件读写 + 代码执行工具。
"""
from crewai import Agent
from ..tools.file_tool import file_write, file_read, push_to_channel
from ..tools.python_tool import python_execute


def create_push_specialist(llm, cfg: dict) -> Agent:
    """创建新闻推送专员（配置来自 config/agents/push_specialist.yaml）"""
    return Agent(
        role=cfg["role"],
        goal=cfg["goal"],
        backstory=cfg["backstory"],
        tools=[file_write, file_read, push_to_channel, python_execute],
        llm=llm,
        allow_delegation=cfg.get("allow_delegation", False),
        verbose=cfg.get("verbose", True),
        max_iter=cfg.get("max_iter", 25),
        checkpoint=True,
        # memory 需要 embedding 模型，当前网关不支持，故关闭
        memory=False,
    )
