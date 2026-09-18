"""
新闻团队 - Crew 编排
从 YAML 加载 agents 和 tasks 配置，组装成 Crew 并运行。
"""
import os
from pathlib import Path

import yaml
from crewai import Agent, Crew, Process, Task
from dotenv import load_dotenv

from .agents.toutiao_collector import create_toutiao_collector
from .agents.weibo_collector import create_weibo_collector
from .agents.zhihu_collector import create_zhihu_collector
from .agents.content_integrator import create_content_integrator
from .agents.heat_ranker import create_heat_ranker
from .agents.comment_analyst import create_comment_analyst
from .agents.push_specialist import create_push_specialist

# 加载环境变量
load_dotenv()

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = BASE_DIR / "config"


def load_yaml(filename: str) -> dict:
    """从 config 目录加载单个 YAML 文件"""
    path = CONFIG_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_yaml_dir(dirname: str) -> dict:
    """
    遍历 config 子目录加载所有 YAML 文件。
    返回 {文件名(不含扩展名): 配置内容} 的字典。
    """
    configs = {}
    dir_path = CONFIG_DIR / dirname
    for file in sorted(dir_path.glob("*.yaml")):
        key = file.stem
        with open(file, "r", encoding="utf-8") as f:
            configs[key] = yaml.safe_load(f)
    return configs


def get_llm():
    """根据环境变量创建 LLM"""
    from crewai import LLM

    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL_NAME", "gpt-4o")
    base_url = os.getenv("OPENAI_API_BASE")

    kwargs = {"model": model}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return LLM(**kwargs)


def build_crew() -> Crew:
    """从 YAML 配置构建 Crew"""
    # 每个 agent / task 一个独立 YAML 文件
    agents_config = load_yaml_dir("agents")
    tasks_config = load_yaml_dir("tasks")

    llm = get_llm()

    # ---- 创建 agents（配置来自各自独立的 YAML 文件）----
    agents = {
        "toutiao_collector": create_toutiao_collector(llm, agents_config["toutiao_collector"]),
        "weibo_collector": create_weibo_collector(llm, agents_config["weibo_collector"]),
        "zhihu_collector": create_zhihu_collector(llm, agents_config["zhihu_collector"]),
        "content_integrator": create_content_integrator(llm, agents_config["content_integrator"]),
        "heat_ranker": create_heat_ranker(llm, agents_config["heat_ranker"]),
        "comment_analyst": create_comment_analyst(llm, agents_config["comment_analyst"]),
        "push_specialist": create_push_specialist(llm, agents_config["push_specialist"]),
    }

    # ---- 创建 tasks ----
    def make_task(key: str) -> Task:
        cfg = tasks_config[key]
        agent_key = cfg["agent"]
        return Task(
            description=cfg["description"],
            expected_output=cfg["expected_output"],
            agent=agents[agent_key],
            context=[make_task(c) for c in cfg.get("context", [])],
        )

    tasks = [make_task(key) for key in tasks_config.keys()]

    # ---- 组装 Crew ----
    return Crew(
        agents=list(agents.values()),
        tasks=tasks,
        process=Process.sequential,
        verbose=True,
    )


def run():
    """运行新闻团队"""
    crew = build_crew()
    result = crew.kickoff()
    print("\n===== 最终结果 =====")
    print(result)
    return result


if __name__ == "__main__":
    run()
