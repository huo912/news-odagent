"""
新闻团队入口

用法：
    python -m news_crew.main                          # 启动全部 agent
    python -m news_crew.main --agents toutiao_collector weibo_collector
    python -m news_crew.main --list-agents            # 列出可用 agent
    python -m news_crew.main --list-checkpoints       # 列出 checkpoint
    python -m news_crew.main --resume                 # 从最新 checkpoint 断点续跑
    python -m news_crew.main --resume <checkpoint文件> # 从指定 checkpoint 恢复
"""
from .crew import main, run

if __name__ == "__main__":
    main()
