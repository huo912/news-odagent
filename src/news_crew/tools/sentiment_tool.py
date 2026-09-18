"""
情感分析工具 - 供评论分析员使用
对文本进行情感分析，返回正面/负面/中立情绪判断。
"""
from crewai.tools import tool
from textblob import TextBlob


@tool("sentiment_analyze")
def sentiment_analyze(text: str) -> str:
    """
    对文本进行情感分析，返回情绪倾向（正面/负面/中立）及极性分数。
    参数:
        text: 要分析的文本
    返回:
        JSON 格式的情感分析结果
    """
    import json
    blob = TextBlob(text)
    polarity = blob.sentiment.polarity  # -1.0 ~ 1.0
    subjectivity = blob.sentiment.subjectivity  # 0.0 ~ 1.0

    if polarity > 0.1:
        sentiment = "正面"
    elif polarity < -0.1:
        sentiment = "负面"
    else:
        sentiment = "中立"

    return json.dumps({
        "sentiment": sentiment,
        "polarity": round(polarity, 3),
        "subjectivity": round(subjectivity, 3),
    }, ensure_ascii=False)
