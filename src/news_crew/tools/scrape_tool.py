"""
网页抓取工具 - 供采集员使用
抓取指定 URL 的网页内容，提取正文文本。
"""
from crewai.tools import tool
import requests
from bs4 import BeautifulSoup


@tool("web_scrape")
def web_scrape(url: str) -> str:
    """
    抓取指定 URL 的网页内容，返回提取后的正文文本。
    参数:
        url: 要抓取的网页地址
    返回:
        网页正文文本（去除 HTML 标签）
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # 移除脚本和样式
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # 限制长度避免超出上下文
        return text[:8000]
    except Exception as e:
        return f"抓取失败: {e}"
