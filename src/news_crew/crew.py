"""
新闻团队 - Crew 编排
从 YAML 加载 agents 和 tasks 配置，组装成 Crew 并运行。

断点续跑：
    每个任务完成后自动保存 checkpoint 到 ./.checkpoints/main/。
    中断后执行 `python -m news_crew.main --resume` 可从断点继续
    （跳过已完成任务）；`--list-checkpoints` 查看历史 checkpoint，
    `--resume <文件>` 可指定从某个 checkpoint 恢复。
"""
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

# Agent 工厂注册表：agent key -> 工厂函数
AGENT_FACTORIES = {
    "toutiao_collector": create_toutiao_collector,
    "weibo_collector": create_weibo_collector,
    "zhihu_collector": create_zhihu_collector,
    "content_integrator": create_content_integrator,
    "heat_ranker": create_heat_ranker,
    "comment_analyst": create_comment_analyst,
    "push_specialist": create_push_specialist,
}

# 所有可用 agent（供 CLI --list-agents 展示）
AVAILABLE_AGENTS = list(AGENT_FACTORIES)


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


def get_llm(agent_cfg: dict | None = None, agent_key: str | None = None):
    """
    创建 LLM。

    优先级（高 -> 低）：
        Nacos 远程配置 agents.<agent_key> > Nacos 远程配置 default
        > agent YAML 中的 llm 段 > .env 环境变量。
    未配置 Nacos（NACOS_SERVER_ADDR 为空）时，行为与原来一致：
    agent YAML llm 段 > .env 默认值。

    agent YAML 示例：
        llm:
          model: qwen3.8-27b
          # api_key: sk-xxx
          # base_url: http://10.105.1.5:4001/v1
          # reasoning_effort: low
          # timeout: 600

    返回 DynamicLLM 包装器：每次 agent 调用 LLM 前都会从 Nacos 读取最新配置，
    实现运行中热切换模型。未配置 Nacos 时行为与原来完全一致。
    """
    from .dynamic_llm import DynamicLLM

    return DynamicLLM(agent_key=agent_key, agent_cfg=agent_cfg)


def build_crew(agents: list[str] | None = None) -> Crew:
    """
    从 YAML 配置构建 Crew。

    agents: 要启动的 agent 列表（key，如 "toutiao_collector"）。
        - None 或空列表：启动全部 agent（默认行为）。
        - 指定列表：只创建列表中的 agent；
          未启动 agent 对应的任务自动跳过；
          下游任务部分上游可用时用可用上游继续执行（context 自动裁剪），
          全部上游被跳过时一并跳过。
    """
    # 每个 agent / task 一个独立 YAML 文件
    agents_config = load_yaml_dir("agents")
    tasks_config = load_yaml_dir("tasks")

    # ---- 确定要启动的 agent ----
    if agents is None or len(agents) == 0:
        selected = list(AGENT_FACTORIES)
    else:
        unknown = [a for a in agents if a not in AGENT_FACTORIES]
        if unknown:
            raise ValueError(
                f"未知的 agent: {unknown}，可用 agent: {AVAILABLE_AGENTS}"
            )
        # 去重且保持顺序
        selected = list(dict.fromkeys(agents))

    # ---- 创建 agents（配置来自各自独立的 YAML 文件）----
    # 每个 agent 的模型配置优先级：
    # Nacos 远程配置（agents.<key> > default）> YAML llm 段 > .env 默认值。
    # 未配置 Nacos 时回退到原有行为。
    agents = {}
    for key in selected:
        factory = AGENT_FACTORIES[key]
        agents[key] = factory(
            get_llm(agents_config[key], agent_key=key), agents_config[key]
        )

    # ---- 确定要执行的任务 ----
    # 规则：
    # 1. 任务所属 agent 未启动 -> 跳过该任务；
    # 2. 任务有 context 依赖时：
    #    - 部分上游可用 -> 用可用上游继续执行（context 自动裁剪）；
    #    - 全部上游被跳过 -> 没有输入数据，一并跳过。
    skipped: list[str] = []
    active: list[str] = []
    resolving: set[str] = set()

    def resolve(key: str) -> bool:
        """返回任务是否执行；被跳过时记录原因"""
        if key in skipped or key in active:
            return key in active
        if key in resolving:
            raise ValueError(f"任务存在循环依赖: {key}")
        resolving.add(key)
        cfg = tasks_config[key]
        if cfg["agent"] not in agents:
            skipped.append(key)
            return False
        deps = cfg.get("context", [])
        for dep in deps:
            resolve(dep)
        if deps and not any(dep in active for dep in deps):
            skipped.append(key)
            return False
        active.append(key)
        return True

    for key in tasks_config.keys():
        resolve(key)

    if not active:
        raise ValueError(
            f"所选 agent {selected} 没有可执行的任务。\n"
            f"被跳过的任务: {skipped}\n"
            f"提示：下游任务依赖上游任务的输出（context），"
            f"上游 agent 未启动时下游任务会一并跳过。"
            f"请同时启动上游 agent，或改用 --list-agents 查看可用 agent。"
        )

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
            context=[make_task(c) for c in cfg.get("context", []) if c in active],
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
            if dep in active:
                visit(dep)
        state[key] = 2
        order.append(key)

    for key in active:
        visit(key)

    tasks = [make_task(key) for key in order]

    if skipped:
        print(f"[跳过任务] {skipped}（所属 agent 未启动或依赖被跳过的任务）")

    # ---- 组装 Crew ----
    # checkpoint=True: 启用断点续跑，Crew 状态（任务输出）保存到 .checkpoints/，
    #   中断后可用 --resume 从断点继续。
    # memory: 跨会话记忆需要 embedding 模型（默认 text-embedding-3-large），
    #   当前 LLM 网关不提供 embedding 服务，故关闭。
    #   若后续网关支持 embedding，可改回 memory=True 并通过
    #   EMBEDDER_MODEL / EMBEDDER_BASE_URL 环境变量指定模型。
    return Crew(
        agents=list(agents.values()),
        tasks=tasks,
        process=Process.sequential,
        verbose=True,
        checkpoint=True,
        memory=False,
    )


def run(agents: list[str] | None = None):
    """
    运行新闻团队。

    agents: 要启动的 agent 列表（key），None 表示启动全部。
    """
    crew = build_crew(agents)
    result = crew.kickoff()
    print("\n===== 最终结果 =====")
    print(result)
    return result


# checkpoint 默认存储位置（与 Crew(checkpoint=True) 的默认 location 一致）
CHECKPOINT_DIR = "./.checkpoints"


def list_checkpoints(location: str = CHECKPOINT_DIR) -> list[Path]:
    """列出所有 checkpoint 文件，按修改时间从旧到新排序"""
    branch_dir = Path(location) / "main"
    if not branch_dir.exists():
        return []
    return sorted(branch_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)


def find_latest_checkpoint(location: str = CHECKPOINT_DIR) -> Path | None:
    """查找最新的 checkpoint 文件，不存在时返回 None"""
    checkpoints = list_checkpoints(location)
    return checkpoints[-1] if checkpoints else None


def resume_from_checkpoint(checkpoint_path: str | None = None):
    """
    从 checkpoint 断点续跑。

    checkpoint_path: checkpoint 文件路径（.checkpoints/main/*.json）。
        None 时自动使用最新的 checkpoint。
    恢复后的 Crew 会跳过已完成的任务，从最后一个未完成的任务继续执行。
    """
    from crewai import Crew
    from crewai.state.checkpoint_config import CheckpointConfig

    path = Path(checkpoint_path) if checkpoint_path else find_latest_checkpoint()
    if path is None or not path.exists():
        raise FileNotFoundError(
            f"未找到 checkpoint 文件: {path}\n"
            f"提示：先正常运行一次（任务完成时会自动保存 checkpoint），"
            f"或用 --list-checkpoints 查看已有 checkpoint。"
        )

    print(f"[断点续跑] 从 checkpoint 恢复: {path}")
    config = CheckpointConfig(restore_from=str(path))
    crew = Crew.from_checkpoint(config)
    result = crew.kickoff()
    print("\n===== 最终结果 =====")
    print(result)
    return result


def main(argv: list[str] | None = None):
    """CLI 入口：支持动态指定启动哪些 agent"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="news-crew",
        description="新闻团队：多 Agent 新闻采集、处理与推送",
    )
    parser.add_argument(
        "--agents",
        nargs="+",
        metavar="AGENT",
        default=None,
        help=(
            "要启动的 agent（可多个，空格分隔），"
            f"可选值: {', '.join(AVAILABLE_AGENTS)}；"
            "不指定则启动全部 agent"
        ),
    )
    parser.add_argument(
        "--list-agents",
        action="store_true",
        help="列出所有可用 agent 后退出",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="",
        default=None,
        metavar="CHECKPOINT",
        help=(
            "从 checkpoint 断点续跑（跳过已完成任务）。"
            "不带参数时使用最新 checkpoint，"
            "也可指定 .checkpoints/main/ 下的具体文件"
        ),
    )
    parser.add_argument(
        "--list-checkpoints",
        action="store_true",
        help="列出所有 checkpoint 后退出",
    )
    args = parser.parse_args(argv)

    if args.list_agents:
        print("可用 agent：")
        for name in AVAILABLE_AGENTS:
            print(f"  - {name}")
        return None

    if args.list_checkpoints:
        checkpoints = list_checkpoints()
        if not checkpoints:
            print(f"暂无 checkpoint（目录: {CHECKPOINT_DIR}/main/）")
        else:
            print(f"checkpoint 列表（{CHECKPOINT_DIR}/main/，从旧到新）：")
            for i, p in enumerate(checkpoints, 1):
                marker = "  <- 最新" if i == len(checkpoints) else ""
                print(f"  {i}. {p}{marker}")
        return None

    # 运行前注册服务到 Nacos（未配置 NACOS_SERVER_ADDR 时静默跳过）
    from .nacos_registry import register_service

    register_service()

    if args.resume is not None:
        return resume_from_checkpoint(args.resume or None)

    return run(args.agents)


if __name__ == "__main__":
    main()
