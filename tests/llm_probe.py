"""
网关延迟探针（tests/llm_probe.py）

目的：
    实测 LLM 网关（http://10.105.1.5:4001/v1，moma_deepseek-v4-flash）
    在干净状态下处理「与推送专员任务等大」prompt 的真实延迟，
    判断端到端测试的 900s 客户端超时是否够用。

做法：
    1. 复用 tests/push_agent_e2e.py 的任务描述构造逻辑（同一 YAML +
       同一 mock 数据 + 同一系统提示），拼出与真实请求等大的 prompt；
    2. 用 openai SDK（与 CrewAI 相同的客户端栈）非流式调用一次，
       max_retries=0（排除 SDK 内部重试干扰），打印耗时与响应摘要；
    3. 同时打印 prompt token 估算，便于横向对比。

运行（项目根目录）：
    .venv/Scripts/python -X utf8 tests/llm_probe.py
"""
import os
import sys
import time
from pathlib import Path

# 保证从项目根目录导入 news_crew
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

from news_crew.crew import _with_date_context, load_yaml
from tests.push_agent_e2e import MOCK_ANALYSIS, TEST_APPENDIX

import json


def build_probe_prompt() -> str:
    """构造与推送专员真实任务等大的 prompt（任务描述 + mock 数据）。"""
    task_cfg = load_yaml("tasks/push_news.yaml")
    mock_json = json.dumps(MOCK_ANALYSIS, ensure_ascii=False, indent=2)
    description = (
        _with_date_context(task_cfg["description"])
        + "\n"
        + TEST_APPENDIX.replace("{mock_json}", mock_json)
    )
    return description


def main() -> int:
    prompt = build_probe_prompt()
    n_chars = len(prompt)
    # 中文约 1 token/字，粗估
    est_tokens = n_chars
    print(f"prompt 长度: {n_chars} 字符（粗估 ~{est_tokens} tokens）")

    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_API_BASE"),
        timeout=900.0,
        max_retries=0,  # 关键：排除 SDK 内部重试，测单次真实延迟
    )

    print("开始非流式调用（max_retries=0，单次尝试，最长等 900s）...")
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL_NAME", "moma_deepseek-v4-flash"),
            messages=[
                {
                    "role": "system",
                    "content": "你是一名新闻推送专员，严格按任务要求执行，"
                    "先输出思考过程，再输出最终答案。",
                },
                {"role": "user", "content": prompt},
            ],
            extra_body={"reasoning_effort": "low"},
        )
    except Exception as e:
        elapsed = time.time() - t0
        print(f"[FAIL] {elapsed:.1f}s 后失败: {type(e).__name__}: {e}")
        return 1

    elapsed = time.time() - t0
    msg = resp.choices[0].message
    content = msg.content or ""
    reasoning = getattr(msg, "reasoning_content", None) or ""
    usage = resp.usage
    print(f"[OK] {elapsed:.1f}s 返回")
    print(f"  completion tokens: {getattr(usage, 'completion_tokens', '?')}")
    print(f"  reasoning 长度: {len(reasoning)} 字符")
    print(f"  content 长度: {len(content)} 字符")
    print(f"  content 前 300 字符:")
    print("  " + content[:300].replace("\n", "\n  "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
