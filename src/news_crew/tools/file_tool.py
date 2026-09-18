"""
文件读写工具 - 供推送专员使用
将推送内容写入文件，或读取文件内容。
"""
from crewai.tools import tool
import os
import json


@tool("file_write")
def file_write(path: str, content: str) -> str:
    """
    将内容写入指定文件。
    参数:
        path: 文件路径
        content: 要写入的内容
    返回:
        写入成功确认
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入文件: {path}"


@tool("file_read")
def file_read(path: str) -> str:
    """
    读取指定文件的内容。
    参数:
        path: 文件路径
    返回:
        文件内容
    """
    with open(path, "r", encoding="utf-8") as f:
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
        # 默认写入文件
        path = os.path.join("output", "push_result.txt")
        os.makedirs("output", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已推送到文件: {path}"
