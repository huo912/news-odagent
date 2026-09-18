"""
搜索工具 - 供采集员使用
封装 Tavily / SerpAPI 等搜索服务，返回结构化搜索结果。
"""
from crewai.tools import tool
import os
import requests


def _tavily_search(query: str, max_results: int = 10) -> list:
    """使用 Tavily 搜索"""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return []
    resp = requests.post(
        "https://api.tavily.com/search",
        json={"api_key": api_key, "query": query, "max_results": max_results},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "content": r.get("content", "")}
        for r in data.get("results", [])
    ]


def _serpapi_search(query: str, max_results: int = 10) -> list:
    """使用 SerpAPI 搜索"""
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        return []
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": max_results,
    }
    resp = requests.get("https://serpapi.com/search", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    results = []
    for r in data.get("organic_results", [])[:max_results]:
        results.append({
            "title": r.get("title", ""),
            "url": r.get("link", ""),
            "content": r.get("snippet", ""),
        })
    return results


@tool("web_search")
def web_search(query: str, max_results: int = 10) -> str:
    """
    搜索互联网，返回结构化搜索结果列表。
    参数:
        query: 搜索关键词
        max_results: 返回结果数量（默认 10）
    返回:
        JSON 格式的搜索结果列表
    """
    import json
    results = _tavily_search(query, max_results)
    if not results:
        results = _serpapi_search(query, max_results)
    return json.dumps(results, ensure_ascii=False)
