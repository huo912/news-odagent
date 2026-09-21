# 新闻团队（News Crew）

基于 **CrewAI** 的多 Agent 新闻采集、处理与推送团队。

## 团队架构

```
┌─────────────────────────────────────────────────────────┐
│                    新闻采集员（3 agents）                 │
│  今日头条热榜采集员 │ 微博热搜采集员 │ 知乎热榜采集员      │
│  (搜索+网页抓取)    │  (搜索)        │  (搜索)            │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│                    新品处理员（2 agents）                 │
│  内容整合员（去重/合并/标准化，Python执行）                │
│  热度排序员（统一热度分，Top 3，Python执行+计算）          │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│                    新闻输出员（2 agents）                 │
│  评论分析员（舆情/情绪/争议点，搜索+情感分析）             │
│  推送专员（格式化+推送，文件读写+代码执行）                │
└─────────────────────────────────────────────────────────┘
```

## 代码隔离设计

- **每个 agent 独立成文件**：`src/news_crew/agents/*.py`，代码互不混合
- **共享工具层**：`src/news_crew/tools/*.py`，每个工具独立文件
- **YAML 配置**：`config/agents/*.yaml` 和 `config/tasks/*.yaml`，每个 agent / task 一个独立文件
- **编排层**：`src/news_crew/crew.py` 遍历目录加载 YAML 并组装 Crew

## 目录结构

```
news-odagent/
├── config/
│   ├── agents/              # 每个 agent 一个独立 YAML 文件
│   │   ├── toutiao_collector.yaml
│   │   ├── weibo_collector.yaml
│   │   ├── zhihu_collector.yaml
│   │   ├── content_integrator.yaml
│   │   ├── heat_ranker.yaml
│   │   ├── comment_analyst.yaml
│   │   └── push_specialist.yaml
│   └── tasks/               # 每个 task 一个独立 YAML 文件
│       ├── collect_toutiao.yaml
│       ├── collect_weibo.yaml
│       ├── collect_zhihu.yaml
│       ├── integrate_content.yaml
│       ├── rank_by_heat.yaml
│       ├── analyze_comments.yaml
│       └── push_news.yaml
├── src/news_crew/
│   ├── agents/              # 7 个 agent，各自独立文件
│   │   ├── toutiao_collector.py
│   │   ├── weibo_collector.py
│   │   ├── zhihu_collector.py
│   │   ├── content_integrator.py
│   │   ├── heat_ranker.py
│   │   ├── comment_analyst.py
│   │   └── push_specialist.py
│   ├── tools/               # 共享工具层
│   │   ├── search_tool.py   # 搜索
│   │   ├── scrape_tool.py   # 网页抓取
│   │   ├── python_tool.py   # Python 代码执行
│   │   ├── sentiment_tool.py# 情感分析
│   │   └── file_tool.py     # 文件读写/推送
│   ├── crew.py              # Crew 编排（加载 YAML）
│   └── main.py              # 入口
├── .env.example
├── pyproject.toml
└── README.md
```

## 安装

> **环境要求**：需要 **Python 3.10–3.12**（crewai 依赖的 numpy 在 Python 3.13+ 上无预编译 wheel，安装会失败）。

```bash
# 1. 创建虚拟环境（需 Python 3.10-3.12）
python -m venv .venv
# Windows
source .venv/Scripts/activate
# macOS/Linux
source .venv/bin/activate

# 2. 安装依赖
pip install -e .

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY 等
```

## 运行

```bash
python -m news_crew.main
# 或
news-crew
```

## 各 Agent 说明

| Agent          | 工具              | 性格背景             |
| -------------- | ----------------- | -------------------- |
| 今日头条采集员 | 搜索+网页抓取     | 外倾性高，热点敏感   |
| 微博热搜采集员 | 搜索              | 外倾性高，热点敏感   |
| 知乎热榜采集员 | 搜索              | 开放性高，深度讨论   |
| 内容整合员     | Python 执行       | 尽责性极高，数据清洗 |
| 热度排序员     | Python 执行+计算  | 神经质高，严谨排序   |
| 评论分析员     | 搜索+情感分析     | 宜人性高，客观中立   |
| 推送专员       | 文件读写+代码执行 | 外倾性高，简洁表达   |

## 输出

- 采集结果：各平台 Top 10（含新闻内容摘要 summary）
- 处理结果：跨平台新闻池 + 全平台 Top 3（保留 summary）
- 输出结果：舆情分析 + 评论分析员撰写的自己的评论（own_comment）+ 推送内容（Markdown/JSON/纯文本）

推送 JSON 结构（`output/push_result.txt`）：

```json
{
  "type": "news_sentiment_top3",
  "generated_at": "YYYY-MM-DD HH:MM",
  "channel": "file",
  "note": "可选，说明数据缺失或异常情况",
  "items": [
    {
      "rank": 1,
      "source": "平台名",
      "heat_score": 12345,
      "title": "新闻标题",
      "summary": "新闻内容摘要",
      "sentiment_summary": "舆情总结",
      "positive_points": [],
      "negative_points": [],
      "neutral_points": [],
      "core_views": "核心观点",
      "controversy_points": [],
      "own_comment": "评论分析员撰写的自己的评论"
    }
  ]
}
```
