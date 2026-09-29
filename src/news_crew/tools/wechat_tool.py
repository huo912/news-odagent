"""
微信公众号发布工具 - 供推送专员使用
通过公众号官方 API（草稿箱 + 发布能力）发表文章；头条号后台开启
「内容源同步」并绑定该公众号后，已发表文章会自动同步到头条号，
从而绕开头条号无公开发布 API 的限制。

链路：wechat_publish -> 公众号发表 --内容源同步(官方)--> 头条号

前置条件（详见 README「推送到头条号」章节）：
1. 已认证的订阅号/服务号（未认证个人订阅号无草稿/发布 API 权限，
   调用会返回 48001）。
2. 公众号后台「设置与开发 → 基本配置 → IP 白名单」加入运行机出口 IP
   （报错 40164 的 errmsg 会带上实际 IP）。
3. 头条号后台「内容源同步」绑定该公众号（一次性配置）。

环境变量：
    WECHAT_APPID            公众号 AppID（与 SECRET 都配置才启用发布）
    WECHAT_SECRET           公众号 AppSecret
    WECHAT_THUMB_MEDIA_ID   封面图永久素材 media_id（推荐：用 upload-cover
                            命令上传一次后填入）
    WECHAT_THUMB_IMAGE_PATH 未填 media_id 时，从此路径自动上传封面图
    WECHAT_AUTHOR           文章作者名（默认 news-odagent）

CLI 辅助命令：
    python -m news_crew.tools.wechat_tool check
        自检：配置是否齐全、能否获取 access_token
    python -m news_crew.tools.wechat_tool upload-cover <图片路径>
        上传封面图为永久素材，打印 media_id
"""
import json
import os
import re
import sys
import time

import requests
from crewai.tools import tool

_API = "https://api.weixin.qq.com/cgi-bin"

# access_token 进程内缓存（token / 过期时间戳）
_token_cache = {"token": "", "expires_at": 0.0}


def _is_configured() -> bool:
    """WECHAT_APPID 与 WECHAT_SECRET 均已配置时返回 True。"""
    return bool(os.getenv("WECHAT_APPID") and os.getenv("WECHAT_SECRET"))


def _get_token() -> str:
    """获取 access_token（缓存复用，提前 5 分钟过期）。失败抛 RuntimeError。"""
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 300:
        return _token_cache["token"]
    resp = requests.get(
        f"{_API}/token",
        params={
            "grant_type": "client_credential",
            "appid": os.getenv("WECHAT_APPID", ""),
            "secret": os.getenv("WECHAT_SECRET", ""),
        },
        timeout=15,
    )
    data = resp.json()
    if "access_token" not in data:
        # 常见错误：40001 Secret 有误；40164 IP 不在白名单（errmsg 含实际 IP）
        raise RuntimeError(
            f"获取 access_token 失败: {json.dumps(data, ensure_ascii=False)}"
        )
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + int(data.get("expires_in", 7200))
    return _token_cache["token"]


def _api_post(path: str, payload: dict) -> dict:
    """调用 POST JSON 接口并检查 errcode，失败抛 RuntimeError。"""
    resp = requests.post(
        f"{_API}/{path}",
        params={"access_token": _get_token()},
        json=payload,
        timeout=30,
    )
    data = resp.json()
    if data.get("errcode", 0) != 0:
        # 常见错误：48001 未认证账号无 API 权限；45009 超每日发布限额
        raise RuntimeError(f"接口 {path} 失败: {json.dumps(data, ensure_ascii=False)}")
    return data


def _upload_cover(image_path: str) -> str:
    """上传封面图为永久素材，返回 media_id。"""
    path = os.path.abspath(image_path)
    if not os.path.exists(path):
        raise RuntimeError(f"封面图不存在: {path}")
    with open(path, "rb") as f:
        resp = requests.post(
            f"{_API}/material/add_material",
            params={"access_token": _get_token(), "type": "image"},
            files={"media": (os.path.basename(path), f)},
            timeout=60,
        )
    data = resp.json()
    if "media_id" not in data:
        raise RuntimeError(f"上传封面失败: {json.dumps(data, ensure_ascii=False)}")
    return data["media_id"]


def _ensure_thumb_media_id() -> str:
    """封面 media_id：优先环境变量，否则从本地图片自动上传。"""
    media_id = os.getenv("WECHAT_THUMB_MEDIA_ID")
    if media_id:
        return media_id
    image = os.getenv("WECHAT_THUMB_IMAGE_PATH")
    if image:
        return _upload_cover(image)
    raise RuntimeError(
        "缺少封面图素材：请配置 WECHAT_THUMB_MEDIA_ID，或执行 "
        "`python -m news_crew.tools.wechat_tool upload-cover <图片路径>` "
        "上传后填入 .env"
    )


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(text: str) -> str:
    """行内 Markdown：链接/加粗/斜体/行内代码（先转义 HTML）。"""
    t = _escape_html(text)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*([^*\s][^*]*)\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return t


def _md_to_html(md: str) -> str:
    """
    轻量 Markdown → 公众号可用 HTML。
    支持标题/加粗/斜体/链接/行内代码/有序无序列表/引用/分隔线/段落，
    不引入额外依赖。
    """
    list_tag = None  # 当前打开的列表标签 "ul" / "ol"
    html = []

    def close_list():
        nonlocal list_tag
        if list_tag:
            html.append(f"</{list_tag}>")
            list_tag = None

    for raw in md.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            close_list()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            close_list()
            level = min(len(m.group(1)) + 1, 6)  # # 映射为 h2，正文少用 h1
            html.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue
        if re.match(r"^[-*_]{3,}$", line):
            close_list()
            html.append("<hr/>")
            continue
        if line.startswith(">"):
            close_list()
            html.append(
                f"<blockquote><p>{_inline(line.lstrip('> ').strip())}</p></blockquote>"
            )
            continue
        m = re.match(r"^[-*+]\s+(.*)$", line)
        if m:
            if list_tag != "ul":
                close_list()
                html.append("<ul>")
                list_tag = "ul"
            html.append(f"<li>{_inline(m.group(1))}</li>")
            continue
        m = re.match(r"^\d+[.、]\s+(.*)$", line)
        if m:
            if list_tag != "ol":
                close_list()
                html.append("<ol>")
                list_tag = "ol"
            html.append(f"<li>{_inline(m.group(1))}</li>")
            continue
        close_list()
        html.append(f"<p>{_inline(line)}</p>")
    close_list()
    return "\n".join(html)


def _wait_publish(publish_id: str, rounds: int = 4, interval: int = 3) -> dict:
    """轮询发布状态，尽早发现即时失败（内容被拒等）。返回最后一次响应。"""
    last = {}
    for _ in range(rounds):
        resp = requests.post(
            f"{_API}/freepublish/get",
            params={"access_token": _get_token()},
            json={"publish_id": publish_id},
            timeout=15,
        )
        last = resp.json()
        # publish_state: 0 成功 / 1 发布中 / 其他 失败
        if last.get("errcode", 0) != 0 or last.get("publish_state") != 1:
            break
        time.sleep(interval)
    return last


def _article_url(detail: dict) -> str:
    """从发布状态响应中提取文章链接（可能不存在）。"""
    try:
        return detail["article_detail"]["item"][0].get("article_url", "")
    except (KeyError, IndexError, TypeError):
        return ""


@tool("wechat_publish")
def wechat_publish(title: str, content: str, digest: str = "") -> str:
    """
    将一篇文章发布到微信公众号（草稿箱 + 发布能力 API）。
    头条号开启「内容源同步」并绑定该公众号后，文章会自动同步到头条号。
    参数:
        title: 文章标题（不超过 64 字符）
        content: 正文内容，支持 Markdown（自动转为公众号可用 HTML）
        digest: 摘要（可选，不超过 120 字符，用于会话列表与分享卡片）
    返回:
        发布结果确认（含发布状态与文章链接）；未配置或失败时返回
        原因与回退建议
    """
    if not _is_configured():
        return (
            "未配置 WECHAT_APPID/WECHAT_SECRET，无法通过公众号发布。"
            "请改用 push_to_channel 渠道推送文件，并在最终报告中如实说明"
            "本次未发布到公众号/头条号。"
        )
    try:
        title = title.strip()[:64]
        digest = (digest or "").strip()[:120]
        html = _md_to_html(content)[:20000]  # 公众号正文上限约 2 万字符
        draft = _api_post(
            "draft/add",
            {
                "articles": [
                    {
                        "title": title,
                        "author": os.getenv("WECHAT_AUTHOR", "news-odagent"),
                        "digest": digest,
                        "content": html,
                        "thumb_media_id": _ensure_thumb_media_id(),
                        "need_open_comment": 0,
                        "only_fans_can_comment": 0,
                    }
                ]
            },
        )
        media_id = draft["media_id"]
        publish_id = _api_post("freepublish/submit", {"media_id": media_id})[
            "publish_id"
        ]
        detail = _wait_publish(publish_id)
        state = detail.get("publish_state")
        if state == 0:
            url = _article_url(detail)
            return (
                f"已发布到微信公众号：《{title}》（草稿 media_id={media_id}，"
                f"publish_id={publish_id}）。文章链接: {url or '(响应未含链接)'}。"
                "头条号「内容源同步」稍后会自动同步本文，无需其他操作。"
            )
        if state == 1:
            return (
                f"公众号发布已提交，审核中：《{title}》（publish_id={publish_id}）。"
                "审核通过后会自动群发并同步到头条号，请勿重复提交。"
            )
        return (
            f"公众号发布未成功（publish_state={state}，publish_id={publish_id}）: "
            f"{json.dumps(detail, ensure_ascii=False)}。请如实报告该结果，"
            "勿伪造成功；可回退 push_to_channel 渠道。"
        )
    except (RuntimeError, requests.RequestException) as e:
        return (
            f"公众号发布失败：{e}。请如实报告该错误，勿伪造成功；"
            "可回退 push_to_channel 渠道。"
        )


def _cli(argv: list) -> int:
    """CLI 自检与封面上传辅助命令。"""
    from dotenv import load_dotenv

    load_dotenv()
    if not argv or argv[0] not in ("check", "upload-cover"):
        print(__doc__)
        return 1
    if not _is_configured():
        print("未配置 WECHAT_APPID/WECHAT_SECRET（请在 .env 中填写）")
        return 1
    try:
        if argv[0] == "check":
            token = _get_token()
            print(f"access_token 获取成功: {token[:12]}...")
            print(f"封面 media_id: {os.getenv('WECHAT_THUMB_MEDIA_ID') or '(未配置)'}")
            return 0
        if len(argv) < 2:
            print("用法: python -m news_crew.tools.wechat_tool upload-cover <图片路径>")
            return 1
        media_id = _upload_cover(argv[1])
        print(f"封面上传成功，media_id={media_id}")
        print("请将其填入 .env 的 WECHAT_THUMB_MEDIA_ID")
        return 0
    except (RuntimeError, requests.RequestException) as e:
        print(f"失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
