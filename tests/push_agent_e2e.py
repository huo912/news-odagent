"""
推送专员端到端测试实例（tests/push_agent_e2e.py）

目的：
    不运行完整新闻流水线，单独测试推送专员 agent 能否推送到新闻头条：
    注入模拟的「评论分析员」输出（测试数据），让推送专员完成
    格式化 → 写文件（news_push.json/md/txt）→ 调用 toutiao_publish
    真实发布到头条号 → push_to_channel 文件渠道兜底 的完整链路。

前置条件：
    1. .env 中 TOUTIAO_PUBLISH_ENABLED=1
    2. 已登录头条号（python -m news_crew.tools.toutiao_tool login）

运行（项目根目录）：
    .venv/Scripts/python -X utf8 tests/push_agent_e2e.py

注意：
    本测试会【真实发布】一篇标题以「测试」开头的文章到头条号
    （发布过程会弹出 Chromium 窗口），验证完成后请到头条号后台
    「内容管理」删除该测试文章。
"""
import json
import sys

from crewai import Crew, Process, Task

from news_crew.agents.push_specialist import create_push_specialist
from news_crew.crew import _with_date_context, get_llm, load_yaml

# ---- 模拟「评论分析员」输出（测试数据，非真实新闻） ----
MOCK_ANALYSIS = {
    "type": "news_sentiment_top3",
    "generated_at": "2026-09-30 12:00",
    "items": [
        {
            "rank": 1,
            "source": "微博",
            "heat_score": 8523910,
            "title": "国产大模型推理成本降至十分之一",
            "summary": "多家国内 AI 公司本周陆续宣布新一代推理模型上线，"
                       "单位推理成本较上一代下降约 90%，多家云厂商同步下调 "
                       "API 价格，引发行业热议。",
            "sentiment_summary": "整体舆情偏正面，公众对技术进步与降价持欢迎态度；"
                                 "少数声音担忧价格战影响行业长期健康。",
            "positive_points": ["技术进步降低使用门槛", "API 降价利好开发者与中小企业"],
            "negative_points": ["价格战或挤压中小厂商利润空间"],
            "neutral_points": ["各家模型真实能力差异仍需实测检验"],
            "core_views": "降价潮将加速大模型应用落地，行业竞争从价格转向场景。",
            "controversy_points": ["低价策略是否可持续"],
            "own_comment": "推理成本下降是大模型走向普及的关键一步，"
                           "但真正的竞争焦点正在从价格转向场景落地能力。",
        },
        {
            "rank": 2,
            "source": "知乎",
            "heat_score": 6310245,
            "title": "多地宣布中小学 AI 通识课全覆盖",
            "summary": "多个省市教育部门发布规划，宣布在未来一学年内实现中小学 "
                       "AI 通识课全覆盖，配套师资培训同步启动，家长与教师讨论热烈。",
            "sentiment_summary": "舆情总体支持，认为有助于培养下一代数字素养；"
                                 "担忧集中在师资不足与课时挤占。",
            "positive_points": ["提升学生数字素养", "政策配套师资培训"],
            "negative_points": ["师资储备不足", "或挤占现有课时"],
            "neutral_points": ["课程内容与考核标准尚未完全公布"],
            "core_views": "AI 教育普及是趋势，落地质量取决于师资与课程设计。",
            "controversy_points": ["是否增加学生负担"],
            "own_comment": "AI 通识课的方向值得肯定，但成败关键在师资培训的落实速度，"
                           "而非覆盖率的数字。",
        },
        {
            "rank": 3,
            "source": "今日头条",
            "heat_score": 4872190,
            "title": "新型固态电池续航突破 1000 公里",
            "summary": "某车企联合电池厂商发布新一代固态电池量产时间表，宣称整车续航"
                       "突破 1000 公里且低温衰减显著降低，相关话题登上多个平台热榜。",
            "sentiment_summary": "舆情以期待为主，技术派讨论热烈；"
                                 "部分声音对量产时间表持观望态度。",
            "positive_points": ["续航与低温性能提升明显", "量产时间表明确"],
            "negative_points": ["量产时间表存在跳票先例"],
            "neutral_points": ["成本与售价尚未公布"],
            "core_views": "固态电池是下一代电动车的关键技术拐点，量产兑现是最大变量。",
            "controversy_points": ["量产时间表是否过于乐观"],
            "own_comment": "固态电池的参数固然亮眼，但消费者真正等的是量产上车那一刻，"
                           "时间表兑现比发布会更重要。",
        },
    ],
}

# 测试专用附加说明（拼接到 push_news.yaml 的任务描述之后）
TEST_APPENDIX = """
【本次运行为端到端测试】
上游「评论分析员」任务本次未实际执行，其输出（模拟测试数据）已直接注入如下，
请将其视为评论分析员的正式输出，并按上述全部规则完成推送：

<analyze_comments_output>
{mock_json}
</analyze_comments_output>

测试专用附加要求：
- 发布到头条号的文章标题以「测试」开头，如
  「测试：新闻舆情 Top3（2026-09-30）」，便于发布后在头条号后台
  「内容管理」中识别并删除本篇测试文章。
- 其余流程与正式运行完全一致：写三个文件 + toutiao_publish 发布 +
  push_to_channel 兜底归档。
"""


def preflight() -> None:
    """发布前自检：开关已启用、会话文件存在，失败则快速退出。"""
    from news_crew.tools.toutiao_tool import _enabled, _state_path

    if not _enabled():
        print("[X] TOUTIAO_PUBLISH_ENABLED 未设为 1，推送专员将无法发布到头条号。")
        print("    请在 .env 中设置 TOUTIAO_PUBLISH_ENABLED=1 后重试。")
        sys.exit(1)
    state = _state_path()
    if not state.exists():
        print(f"[X] 未找到头条号会话文件 {state}。")
        print("    请先执行: python -m news_crew.tools.toutiao_tool login")
        sys.exit(1)
    print(f"[OK] 头条号发布已启用，会话文件存在: {state}")


def main() -> int:
    preflight()

    agent_cfg = load_yaml("agents/push_specialist.yaml")
    task_cfg = load_yaml("tasks/push_news.yaml")

    print("=" * 60)
    print("推送专员端到端测试：注入模拟舆情数据，真实发布到头条号")
    print(f"模拟数据: {len(MOCK_ANALYSIS['items'])} 条新闻（测试数据）")
    print("=" * 60)

    # 单独创建推送专员（与正式流水线同一工厂与配置）
    agent = create_push_specialist(
        get_llm(agent_cfg, agent_key="push_specialist"), agent_cfg
    )

    # 复用正式任务描述（含全部推送规则），再拼接测试数据与测试附加要求
    mock_json = json.dumps(MOCK_ANALYSIS, ensure_ascii=False, indent=2)
    description = (
        _with_date_context(task_cfg["description"])
        + "\n"
        + TEST_APPENDIX.replace("{mock_json}", mock_json)
    )
    task = Task(
        description=description,
        expected_output=task_cfg["expected_output"],
        agent=agent,
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
        memory=False,  # 网关无 embedding 模型，与正式流水线一致关闭
    )

    result = crew.kickoff()

    print("\n" + "=" * 60)
    print("最终输出")
    print("=" * 60)
    print(result)
    print("\n提示：若 toutiao_publish 发布成功，请到头条号后台「内容管理」"
          "删除本篇测试文章。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
