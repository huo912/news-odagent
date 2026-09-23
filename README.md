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
│   ├── dynamic_llm.py       # 运行中热切换模型的 LLM 包装器
│   ├── nacos_config.py      # Nacos 远程模型配置（可选）
│   ├── nacos_registry.py    # Nacos 服务注册（可选）
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
# 启动全部 agent（默认）
python -m news_crew.main
# 或
news-crew

# 动态指定启动哪些 agent（空格分隔，可多个）
python -m news_crew.main --agents toutiao_collector weibo_collector
news-crew --agents content_integrator heat_ranker

# 列出所有可用 agent
python -m news_crew.main --list-agents
```

### 动态 Agent 选择规则

- `--agents` 不指定时启动全部 7 个 agent（默认行为）。
- 只创建所选 agent；**未启动 agent 对应的任务自动跳过**。
- 下游任务依赖上游任务的输出（`context`）：
  - **部分上游可用** → 用可用上游继续执行，`context` 自动裁剪；
  - **全部上游被跳过** → 没有输入数据，下游任务一并跳过。
  - 跳过的任务会打印 `[跳过任务]` 提示。
- 所选 agent 没有任何可执行任务时（例如只启动下游 agent 而未启动上游），
  会报错并列出被跳过的任务及原因。

示例：

| 命令                                                        | 实际执行的任务                                                                         |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `--agents toutiao_collector weibo_collector`                | 今日头条采集、微博采集                                                                 |
| `--agents toutiao_collector content_integrator heat_ranker` | 今日头条采集 → 内容整合 → 热度排序（微博/知乎采集被跳过，整合任务的 context 自动裁剪） |
| `--agents heat_ranker`                                      | 报错：上游采集/整合 agent 未启动，无可执行任务                                         |

## 断点续跑（Checkpoint）

Crew 已启用 `checkpoint=True`：

- **Checkpoint 断点续跑**：每个任务完成后，Crew 运行时状态（含各任务输出）
  自动保存到 `./.checkpoints/main/{时间戳}_{id}.json`。运行中断（网络异常、
  手动 Ctrl+C 等）后可从断点恢复，**已完成的任务不会重跑**。

> **关于 Memory（跨会话记忆）**：当前已关闭（`memory=False`）。
> CrewAI 的 memory 依赖 embedding 模型（默认 `text-embedding-3-large`），
> 而当前 LLM 网关不提供 embedding 服务，开启会导致 `search_memory`
> 工具报 400 错误。若后续网关支持 embedding，可在 `crew.py` 中改回
> `memory=True`，并通过 `EMBEDDER_MODEL` / `EMBEDDER_BASE_URL` 环境变量
> 指定可用的 embedding 模型。

### 用法

```bash
# 列出所有 checkpoint（按时间从旧到新，最新在最后）
python -m news_crew.main --list-checkpoints

# 从最新 checkpoint 断点续跑（自动跳过已完成任务）
python -m news_crew.main --resume

# 从指定 checkpoint 文件恢复
python -m news_crew.main --resume .checkpoints/main/20260922_123456_ab12cd34_p-xxx.json
```

### 代码调用

```python
from crewai import Crew
from crewai.state.checkpoint_config import CheckpointConfig

config = CheckpointConfig(restore_from=".checkpoints/main/xxx.json")
crew = Crew.from_checkpoint(config)
result = crew.kickoff()   # 从最后一个已完成的任务之后继续
```

### 注意事项

- 需要先**正常运行一次**产生 checkpoint 后才能续跑（`--list-checkpoints`
  可查看是否已有 checkpoint）。
- 恢复时 agent 配置（YAML）最好与保存时一致，否则可能出现任务对不上的情况。
- `.checkpoints/` 是运行时状态目录，建议加入 `.gitignore`。

## 远程模型配置（Nacos，可选）

Agent 使用的模型支持通过 **Nacos 配置中心**远程指定。未配置 Nacos 时，
行为与原来完全一致（agent YAML 的 `llm` 段 > `.env` 环境变量）。

### 启用步骤

```bash
# 1. 安装 Nacos SDK（可选依赖）
pip install -e ".[nacos]"

# 2. 在 .env 中配置 Nacos 连接信息
NACOS_SERVER_ADDR=127.0.0.1:8848
NACOS_NAMESPACE=            # 命名空间 ID，留空为 public
NACOS_GROUP=DEFAULT_GROUP
NACOS_DATA_ID=news-crew-llm.yaml
# NACOS_USERNAME=           # 需要鉴权时填写
# NACOS_PASSWORD=
# NACOS_TIMEOUT_MS=10000    # Nacos 请求超时（毫秒）
```

### Nacos 配置内容

在 Nacos 控制台创建配置（dataId 默认 `news-crew-llm.yaml`，分组默认
`DEFAULT_GROUP`），内容支持 YAML 或 JSON：

```yaml
# 全局默认（作用于所有 agent）
default:
  model: qwen3.8-27b
  # api_key: sk-xxx
  # base_url: http://10.105.1.5:4001/v1
  # reasoning_effort: low
  # timeout: 600

# 按 agent 覆盖（key 为 agent 名）
agents:
  toutiao_collector:
    model: gpt-4o
```

### 模型配置优先级（高 → 低）

1. Nacos `agents.<agent_key>`（按 agent 覆盖）
2. Nacos `default`（全局默认）
3. agent YAML 中的 `llm` 段
4. `.env` 环境变量（`OPENAI_MODEL_NAME` 等）

说明：

- **热更新（运行中热切换）**：首次拉取后，后台线程持续监听 Nacos 配置变更。
  在 Nacos 控制台修改配置并发布后，进程内缓存自动刷新。Agent 通过
  `DynamicLLM` 包装器（`src/news_crew/dynamic_llm.py`）在**每次调用 LLM 前**
  重新读取 Nacos 最新配置，配置有变化时重建底层 LLM，因此**运行中的 agent
  也会立即切换到新模型，无需重启进程、无需等待任务结束**。
- 首次拉取失败（网络异常、配置格式错误等）会打印警告并自动回退到本地
  配置，不阻断启动；后续配置变更仍会尝试热更新。
- 未设置 `NACOS_SERVER_ADDR` 时不会连接 Nacos，也无需安装 SDK。

## 服务注册（Nacos，可选）

进程启动时会把自身作为**临时实例**注册到 Nacos **注册中心**，可在 Nacos
控制台「服务管理 → 服务列表」中看到本服务；进程退出时自动注销。
未配置 `NACOS_SERVER_ADDR` 时静默跳过，不影响运行。

服务注册与上面的远程模型配置**共用同一套连接参数**（server addr /
namespace / username / password），配置好远程模型即自动启用服务注册。

### 相关环境变量

```bash
NACOS_SERVICE_NAME=news-odagent    # 注册的服务名（默认 news-odagent）
NACOS_SERVICE_GROUP=DEFAULT_GROUP  # 服务分组（默认 DEFAULT_GROUP）
NACOS_SERVICE_IP=                  # 注册 IP，留空自动探测本机出口 IP
NACOS_SERVICE_PORT=8000            # 注册端口（批处理任务无监听端口，
                                   # 仅作实例标识，可在控制台查看）
```

### 说明

- 采用 **临时实例（ephemeral=true）**，由 SDK 的 gRPC 长连接维持心跳，
  实例在控制台显示为健康；进程退出时通过 `atexit` 显式注销，即使未
  显式注销，长连接断开后 Nacos 也会自动摘除该实例。
- 注册在独立守护线程中完成，**非阻塞**，不拖慢任务启动；注册失败仅打印
  警告，不影响 Crew 运行。
- 实例 metadata 携带 `app=news-odagent`、`framework=crewai`，便于识别。

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
