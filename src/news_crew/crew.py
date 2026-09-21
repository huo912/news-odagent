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


def get_llm(agent_cfg: dict | None = None):
    """
    创建 LLM。

    优先级：agent YAML 中的 llm 段 > .env 环境变量。
    agent 未配置 llm 段（或某项未配置）时，回退到 .env 默认值。

    agent YAML 示例：
        llm:
          model: qwen3.8-27b
          # api_key: sk-xxx
          # base_url: http://10.105.1.5:4001/v1
          # reasoning_effort: low
          # timeout: 600
    """
    from crewai import LLM

    cfg = (agent_cfg or {}).get("llm") or {}
    api_key = cfg.get("api_key") or os.getenv("OPENAI_API_KEY")
    model = cfg.get("model") or os.getenv("OPENAI_MODEL_NAME", "gpt-4o")
    base_url = cfg.get("base_url") or os.getenv("OPENAI_API_BASE")
    # 推理强度（low/medium/high）：qwen3.8-27b 是推理模型，
    # 默认强度下大 prompt 思考时间可能超过客户端读超时导致
    # "Failed to connect to OpenAI API: Request timed out"。
    # 实测 low 可将大 prompt 响应从 ~59s 降到 ~27s。
    reasoning_effort = cfg.get("reasoning_effort") or os.getenv("OPENAI_REASONING_EFFORT", "low")
    timeout = cfg.get("timeout") or os.getenv("OPENAI_TIMEOUT")

    kwargs = {"model": model}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    if timeout:
        kwargs["timeout"] = float(timeout)
    return LLM(**kwargs)


def build_crew() -> Crew:
    """从 YAML 配置构建 Crew"""
    # 每个 agent / task 一个独立 YAML 文件
    agents_config = load_yaml_dir("agents")
    tasks_config = load_yaml_dir("tasks")

    # ---- 创建 agents（配置来自各自独立的 YAML 文件）----
    # 每个 agent 使用各自 YAML 中 llm 段配置的模型；
    # 未配置 llm 段时回退到 .env 中的默认模型。
    agents = {
        "toutiao_collector": create_toutiao_collector(
            get_llm(agents_config["toutiao_collector"]), agents_config["toutiao_collector"]
        ),
        "weibo_collector": create_weibo_collector(
            get_llm(agents_config["weibo_collector"]), agents_config["weibo_collector"]
        ),
        "zhihu_collector": create_zhihu_collector(
            get_llm(agents_config["zhihu_collector"]), agents_config["zhihu_collector"]
        ),
        "content_integrator": create_content_integrator(
            get_llm(agents_config["content_integrator"]), agents_config["content_integrator"]
        ),
        "heat_ranker": create_heat_ranker(
            get_llm(agents_config["heat_ranker"]), agents_config["heat_ranker"]
        ),
        "comment_analyst": create_comment_analyst(
            get_llm(agents_config["comment_analyst"]), agents_config["comment_analyst"]
        ),
        "push_specialist": create_push_specialist(
            get_llm(agents_config["push_specialist"]), agents_config["push_specialist"]
        ),
    }

    # ---- 创建 tasks ----
    # 注意：context 必须引用任务列表中的同一个 Task 实例，
    # 否则下游任务拿不到上游的实际输出（会出现占位值）。
    task_registry: dict[str, Task] = {}

    def make_task(key: str) -> Task:
        if key in task_registry:
            return task_registry[key]
        cfg = tasks_config[key]
        agent_key = cfg["agent"]
        task = Task(
            description=cfg["description"],
            expected_output=cfg["expected_output"],
            agent=agents[agent_key],
            context=[make_task(c) for c in cfg.get("context", [])],
        )
        task_registry[key] = task
        return task

    # 拓扑排序：sequential 流程按列表顺序执行，
    # 必须保证上游任务排在下游任务之前，context 输出才有效。
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(key: str) -> None:
        s = state.get(key, 0)
        if s == 1:
            raise ValueError(f"任务存在循环依赖: {key}")
        if s == 2:
            return
        state[key] = 1
        for dep in tasks_config[key].get("context", []):
            visit(dep)
        state[key] = 2
        order.append(key)

    for key in tasks_config.keys():
        visit(key)

    tasks = [make_task(key) for key in order]

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
