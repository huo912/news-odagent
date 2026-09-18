"""
Python 代码执行工具 - 供处理员使用
在隔离的沙箱中执行 Python 代码，用于数据清洗、标准化、热度计算。
"""
from crewai.tools import tool
import subprocess
import sys
import tempfile
import os


@tool("python_execute")
def python_execute(code: str) -> str:
    """
    在隔离的 Python 环境中执行代码，返回标准输出。
    参数:
        code: 要执行的 Python 代码字符串
    返回:
        代码执行的标准输出结果
    """
    # 使用临时文件执行，避免污染主进程
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout
        if result.stderr:
            output += "\n[STDERR]\n" + result.stderr
        return output[:8000]
    except subprocess.TimeoutExpired:
        return "执行超时（30秒）"
    finally:
        os.unlink(tmp_path)
