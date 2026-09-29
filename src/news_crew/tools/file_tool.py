"""
文件读写工具 - 供推送专员使用
将推送内容写入文件，或读取文件内容。

路径规则：
    相对路径统一保存到项目根目录的 output/ 下（如 news_push.json
    -> output/news_push.json），避免 agent 用相对路径写文件时
    落到进程工作目录（项目根目录）污染仓库；绝对路径保持不变。
"""
from crewai.tools import tool
import os
from pathlib import Path

# 输出目录：项目根/output（本文件位于 src/news_crew/tools/）
OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output"


def _resolve_path(path: str) -> str:
    """相对路径统一解析到 output/ 目录下，绝对路径保持不变。"""
    p = Path(path)
    if not p.is_absolute():
        p = OUTPUT_DIR / p
    return str(p)


@tool("file_write")
def file_write(path: str, content: str) -> str:
    """
    将内容写入指定文件。相对路径会自动保存到 output/ 目录下
    （如 news_push.json 实际写入 output/news_push.json）。
    参数:
        path: 文件路径（相对路径保存到 output/ 下，绝对路径原样使用）
        content: 要写入的内容
    返回:
        写入成功确认
    """
    path = _resolve_path(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入文件: {path}"


@tool("file_read")
def file_read(path: str) -> str:
    """
    读取指定文件的内容。相对路径自动到 output/ 目录下查找
    （与 file_write 的路径规则一致）。
    参数:
        path: 文件路径（相对路径在 output/ 下查找）
    返回:
        文件内容
    """
    with open(_resolve_path(path), "r", encoding="utf-8") as f:
        return f.read()


@tool("push_to_channel")
def push_to_channel(channel: str, content: str) -> str:
    """
    将内容推送到指定渠道（webhook / file）。
    参数:
        channel: 渠道类型，如 'file' 或 webhook URL
        content: 推送内容
    返回:
        推送结果确认
    """
    if channel.startswith("http"):
        import requests
        resp = requests.post(channel, json={"content": content}, timeout=15)
        return f"已推送到 webhook，状态码: {resp.status_code}"
    else:
        # 默认写入文件（output/push_result.txt）
        path = OUTPUT_DIR / "push_result.txt"
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已推送到文件: {path}"
