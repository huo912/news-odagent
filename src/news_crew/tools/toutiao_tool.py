"""
头条号发布工具 - 供推送专员使用
通过 Playwright 自动化 mp.toutiao.com（头条号创作者后台）发布文章。

原理：
    头条号没有面向个人创作者的公开发布 API，本工具用 Playwright 驱动
    Chromium 直接操作头条号后台的图文发布页，模拟人工输入标题/正文
    并点击发布。

登录态：
    首次使用需登录一次（扫码或手机号验证码均可，
    python -m news_crew.tools.toutiao_tool login），
    浏览器会话保存到 .toutiao/state.json（已 gitignore），之后自动复用；
    会话过期时工具返回明确提示，重新登录即可。

依赖（可选组，未安装不影响其他功能）：
    pip install -e ".[toutiao]"
    playwright install chromium

环境变量：
    TOUTIAO_PUBLISH_ENABLED  设为 1 启用；留空/0 时工具返回未启用说明
    TOUTIAO_HEADLESS         1=无头模式（默认 0 有头，便于扫码与观察）
    TOUTIAO_STATE_PATH       会话文件路径（默认 <项目根>/.toutiao/state.json）

CLI：
    python -m news_crew.tools.toutiao_tool login                     # 登录（扫码/手机号）
    python -m news_crew.tools.toutiao_tool check                     # 检查登录态
    python -m news_crew.tools.toutiao_tool publish <标题> <md文件>   # 手动发布
    python -m news_crew.tools.toutiao_tool list [关键词]             # 列出内容管理页文章
                                                                    # （核实发布是否成功）
"""
import os
import re
import time
from pathlib import Path

from crewai.tools import tool

# 项目根目录（本文件位于 src/news_crew/tools/）
BASE_DIR = Path(__file__).resolve().parents[3]

# ---- 头条号创作者后台 URL ----
LOGIN_URL = "https://mp.toutiao.com/auth/page/login/"
PUBLISH_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
MANAGE_URL = "https://mp.toutiao.com/profile_v4/graphic/manage"

# 登录成功后 URL 包含的片段
LOGIN_SUCCESS_FRAGMENTS = (
    "mp.toutiao.com/profile",
    "mp.toutiao.com/dashboard",
    "creator.toutiao.com",
)

# 登录成功后浏览器中会写入的头条 SSO cookie 名（任一存在即视为已登录）。
# 手机号验证码登录的落地页 URL 可能不含任何已知成功片段，
# 此时唯一可靠的判定依据是这些 cookie（登录态的本体）。
LOGIN_COOKIES = (
    "sessionid", "sessionid_ss", "sso_uid", "sid_guard", "sid_tt", "uid_tt",
)

# 发布成功后页面跳转到的 URL 片段
PUBLISH_SUCCESS_FRAGMENTS = (
    "profile_v4/index",
    "profile_v4/graphic/manage",
    "profile_v4/graphic/article_list",
    "article/manage",
)

# 发布失败时页面可能出现的提示文本
PUBLISH_ERROR_TEXTS = (
    "发布失败", "内容违规", "请重试", "审核不通过", "操作失败",
    "标题不能为空", "正文不能为空", "内容不能为空",
)

# 标题输入框选择器（多级回退）
TITLE_SELECTORS = (
    "textarea[placeholder*='请输入文章标题']",
    "textarea[placeholder*='标题']",
    ".article-title textarea",
)

# 发布按钮选择器（多级回退）
PUBLISH_BUTTON_SELECTORS = (
    "button.publish-btn.publish-btn-last",
    "button.publish-btn",
    "xpath=//button[contains(., '预览并发布')]",
    "xpath=//button[normalize-space()='发布']",
)

# 二次确认弹窗中的确认按钮文本
CONFIRM_BUTTON_TEXTS = ("确认发布", "确认发表", "确定发布", "确定", "确认")

# 标题长度上限（头条规则：中文/标点计 1，ASCII 字母数字计 0.5）
MAX_TITLE_WEIGHT = 30

# 通用浏览器 UA（降低自动化风控概率）
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 向 ProseMirror 编辑器写入 HTML 的脚本（多级兜底）：
# 1) ProseMirror view API（正确生成富文本节点）
# 2) execCommand insertHTML（ProseMirror 经 DOM observer 同步）
# 3) 模拟粘贴事件（ProseMirror 有 paste 处理器）
# 4) execCommand insertText 纯文本兜底
_INSERT_CONTENT_JS = """
([html, plainText]) => {
    const el = document.querySelector('.ProseMirror') ||
               document.querySelector("[contenteditable='true']") ||
               document.querySelector("div[role='textbox']");
    if (!el) return 'NO_EDITOR';
    el.focus();

    // 1) ProseMirror view API
    let view = null;
    if (el.pmViewDesc && el.pmViewDesc.view) view = el.pmViewDesc.view;
    else {
        let p = el;
        for (let i = 0; i < 5 && p; i++) {
            if (p.pmViewDesc && p.pmViewDesc.view) { view = p.pmViewDesc.view; break; }
            p = p.parentElement;
        }
    }
    if (view && view.state && view.dispatch) {
        try {
            const parser = view.domParser ||
                (view.state.schema.cached && view.state.schema.cached.domParser);
            if (parser) {
                const temp = document.createElement('div');
                temp.innerHTML = html;
                const slice = parser.parseSlice(temp);
                view.dispatch(view.state.tr.replaceSelection(slice));
                view.focus();
                return 'PM_OK';
            }
        } catch (e) { /* 落入兜底 */ }
    }

    // 2) execCommand insertHTML
    try {
        document.execCommand('selectAll', false, null);
        document.execCommand('delete', false, null);
        if (document.execCommand('insertHTML', false, html)) return 'EXEC_HTML_OK';
    } catch (e) { /* 落入兜底 */ }

    // 3) 模拟粘贴
    try {
        const dt = new DataTransfer();
        dt.setData('text/html', html);
        dt.setData('text/plain', plainText);
        el.dispatchEvent(new ClipboardEvent('paste', {
            clipboardData: dt, bubbles: true, cancelable: true
        }));
        return 'PASTE_OK';
    } catch (e) { /* 落入兜底 */ }

    // 4) 纯文本兜底
    try {
        document.execCommand('selectAll', false, null);
        document.execCommand('delete', false, null);
        document.execCommand('insertText', false, plainText);
        return 'PLAIN_OK';
    } catch (e) {
        return 'FAILED';
    }
}
"""

# 在二次确认弹窗中点击确认按钮的脚本
_CLICK_CONFIRM_JS = """
(texts) => {
    const visible = (el) => {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden'
            && r.width > 0 && r.height > 0;
    };
    const roots = Array.from(document.querySelectorAll(
        '.semi-modal, .byte-modal, [role="dialog"], [class*="modal"], [class*="dialog"]'
    )).filter(visible);
    for (const root of roots) {
        const btns = Array.from(root.querySelectorAll('button')).filter(visible);
        const target = btns.find(b => texts.includes((b.textContent || '').trim()));
        if (target) { target.click(); return (target.textContent || '').trim(); }
    }
    return null;
}
"""


# ---- 配置辅助 ----

def _state_path() -> Path:
    """头条号会话文件路径（storage state，含登录 cookie）。"""
    return Path(os.getenv("TOUTIAO_STATE_PATH") or (BASE_DIR / ".toutiao" / "state.json"))


def _headless() -> bool:
    return os.getenv("TOUTIAO_HEADLESS", "0").strip().lower() in ("1", "true", "yes", "on")


def _enabled() -> bool:
    return os.getenv("TOUTIAO_PUBLISH_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _has_login_cookies(ctx) -> bool:
    """浏览器上下文中是否已存在头条 SSO 登录 cookie。"""
    try:
        names = {c["name"] for c in ctx.cookies()}
    except Exception:
        return False
    return any(n in names for n in LOGIN_COOKIES)


def _session_valid(page, ctx) -> bool:
    """发布页加载后判定登录态是否有效。

    - URL 被重定向回登录页（含 login/auth）→ 会话已失效；
    - 否则：仍在 mp.toutiao.com 域下，或已持有 SSO cookie，即视为有效
      （手机号登录后可能停在后台根路径等不含 profile 字样的 URL）。
    """
    url = page.url
    if "login" in url or "auth" in url:
        return False
    return "mp.toutiao.com" in url or _has_login_cookies(ctx)


# ---- 内容转换 ----

def _title_weight(title: str) -> float:
    """按头条规则计算标题长度：中文/标点计 1，ASCII 字母数字计 0.5，空白不计。"""
    w = 0.0
    for ch in title:
        if ch.isspace():
            continue
        w += 0.5 if ord(ch) < 128 else 1.0
    return w


def _inline_html(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
    return text


def _md_to_html(md: str) -> str:
    """
    轻量 Markdown → HTML（支持 # 标题、**粗体**、- 无序列表、空行分段）。
    输出已做 HTML 实体转义，可直接注入编辑器。
    """
    md = md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out, in_list = [], False
    for raw in md.split("\n"):
        line = raw.strip()
        if not line:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("<p><br></p>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*$", line)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline_html(m.group(2))}</h{level}>")
            continue
        m = re.match(r"^[-*+]\s+(.*)$", line)
        if m:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline_html(m.group(1))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        out.append(f"<p>{_inline_html(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _md_to_plain(md: str) -> str:
    """Markdown → 纯文本（去除标记），作为编辑器写入的最终兜底。"""
    text = re.sub(r"^\s*#{1,6}\s+", "", md, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    return text.replace("**", "").replace("__", "")


# ---- 浏览器操作 ----

def _launch(pw, headless: bool):
    """启动 Chromium 并注入已保存的登录态（如有）。"""
    browser = pw.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    state = _state_path()
    ctx = browser.new_context(
        storage_state=str(state) if state.exists() else None,
        user_agent=_UA,
        viewport={"width": 1440, "height": 900},
    )
    return browser, ctx


def _input_title(page, title: str) -> None:
    """在标题输入框中填入标题（Playwright fill 可正确驱动 React 受控组件）。"""
    for sel in TITLE_SELECTORS:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=5000)
            loc.fill(title)
            return
        except Exception:
            continue
    raise RuntimeError("未找到标题输入框（页面结构可能已改版）")


def _input_content(page, html: str, plain: str) -> None:
    status = page.evaluate(_INSERT_CONTENT_JS, [html, plain])
    if status == "NO_EDITOR":
        raise RuntimeError("未找到正文编辑器（页面结构可能已改版）")


def _verify_content(page) -> None:
    """验证标题/正文确实写入了编辑器，防止静默失败。"""
    title = page.evaluate(
        "() => { const el = document.querySelector(\"textarea[placeholder*='请输入文章标题']\");"
        " return el ? (el.value || '') : ''; }")
    if not str(title).strip():
        raise RuntimeError("标题写入后验证为空")
    content = page.evaluate(
        "() => { const el = document.querySelector('.ProseMirror');"
        " return el ? (el.innerText || '') : ''; }")
    if not str(content).strip():
        raise RuntimeError("正文写入后验证为空")


def _click_publish(page) -> None:
    """点击发布按钮，并轮询处理二次确认弹窗。"""
    sel_used = None
    for sel in PUBLISH_BUTTON_SELECTORS:
        try:
            page.locator(sel).first.wait_for(state="visible", timeout=4000)
            sel_used = sel
            break
        except Exception:
            continue
    if sel_used is None:
        raise RuntimeError("未找到发布按钮（页面结构可能已改版）")
    try:
        page.locator(sel_used).first.click(timeout=8000)
    except Exception:
        # 浮层遮挡时用 JS 按文本匹配兜底点击
        page.evaluate("""() => {
            const visible = (el) => {
                if (!el) return false;
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden'
                    && r.width > 0 && r.height > 0;
            };
            const btn = Array.from(document.querySelectorAll('button'))
                .filter(visible)
                .find(b => ['预览并发布', '发布'].includes((b.textContent || '').trim()));
            if (btn) btn.click();
        }""")

    # 轮询处理二次确认弹窗（最多 8 秒）
    deadline = time.time() + 8
    while time.time() < deadline:
        if any(f in page.url for f in PUBLISH_SUCCESS_FRAGMENTS):
            return  # 已直接发布并跳转，无需确认
        clicked = page.evaluate(_CLICK_CONFIRM_JS, list(CONFIRM_BUTTON_TEXTS))
        if clicked:
            return
        time.sleep(0.5)


def _wait_publish_result(page, timeout: int = 90) -> str:
    """等待发布结果：URL 跳转到管理页 / 成功提示 / 失败提示 / 超时。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(f in page.url for f in PUBLISH_SUCCESS_FRAGMENTS):
            return "发布成功：页面已跳转到内容管理页，文章已提交（审核中）。"
        body = page.evaluate(
            "() => document.body ? (document.body.innerText || '').slice(0, 3000) : ''")
        if "发布成功" in body:
            return "发布成功：文章已提交（审核中）。"
        for err in PUBLISH_ERROR_TEXTS:
            if err in body:
                return f"发布失败：页面提示「{err}」。"
        time.sleep(1)
    return f"发布结果未知（{timeout} 秒内未跳转）：请到头条号后台「内容管理」人工确认。"


def _publish_article(title: str, content_md: str) -> str:
    """完整发布流程：打开发布页 → 输入标题/正文 → 点击发布 → 等待结果。"""
    from playwright.sync_api import sync_playwright

    state = _state_path()
    if not state.exists():
        return ("头条号未登录：请先执行 `python -m news_crew.tools.toutiao_tool login` "
                "扫码登录后再试。")
    if _title_weight(title) > MAX_TITLE_WEIGHT:
        return (f"标题超长（{_title_weight(title):.1f} > {MAX_TITLE_WEIGHT}），"
                "请精简标题后重试。")

    html = _md_to_html(content_md)
    plain = _md_to_plain(content_md)

    with sync_playwright() as pw:
        browser, ctx = _launch(pw, _headless())
        try:
            page = ctx.new_page()
            page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass  # 头条页面有长连接，networkidle 常超时，容忍
            time.sleep(2)

            if not _session_valid(page, ctx):
                return ("头条号登录态已过期：请重新执行 "
                        "`python -m news_crew.tools.toutiao_tool login` 登录。")

            _input_title(page, title)
            _input_content(page, html, plain)
            _verify_content(page)
            _click_publish(page)
            result = _wait_publish_result(page)

            # 发布完成后刷新会话文件（延长 cookie 有效期）
            try:
                state.parent.mkdir(parents=True, exist_ok=True)
                ctx.storage_state(path=str(state))
            except Exception:
                pass
            return result
        finally:
            ctx.close()
            browser.close()


# ---- CrewAI 工具 ----

@tool("toutiao_publish")
def toutiao_publish(title: str, content: str) -> str:
    """
    将一篇 Markdown 文章发布到头条号（mp.toutiao.com 创作者后台）。
    参数:
        title: 文章标题（长度不超过 30：中文计 1、英文数字计 0.5；建议含日期）
        content: 文章正文，Markdown 格式（支持 # 标题、**粗体**、- 列表、空行分段）
    返回:
        发布结果确认（成功 / 失败原因 / 未启用说明）
    说明:
        - 需预先启用（环境变量 TOUTIAO_PUBLISH_ENABLED=1 且已扫码登录）；
          未启用或未登录时返回说明，此时必须回退用 push_to_channel 归档文件；
        - 全部新闻整合为一篇文章发布，严禁拆成多篇。
    """
    if not _enabled():
        return ("头条号发布未启用（TOUTIAO_PUBLISH_ENABLED 未设为 1）。"
                "请改用 push_to_channel 将推送内容归档到文件渠道。")
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return ("playwright 未安装：请执行 `pip install -e \".[toutiao]\"` 与 "
                "`playwright install chromium` 后重试；本次请回退文件渠道。")
    try:
        return _publish_article(title, content)
    except Exception as e:
        return f"头条号发布失败：{e}；请如实记录并回退文件渠道。"


# ---- CLI ----

def _cli_login() -> int:
    """交互式登录：打开浏览器等待扫码或手机号验证码登录，成功后保存会话。

    判定依据（满足任一即认为登录成功）：
    1. URL 跳转到已知成功片段（profile/dashboard/creator）；
    2. 浏览器写入头条 SSO cookie（sessionid 等）——手机号登录的
       落地页 URL 可能不含任何已知片段，cookie 才是登录态本体。
    用户手动关闭浏览器窗口视为放弃，不保存会话。
    """
    from playwright.sync_api import sync_playwright

    state = _state_path()
    state.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=_UA,
                                  viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        print("请在弹出的浏览器中完成登录（扫码或手机号验证码均可，5 分钟内有效）...")
        deadline = time.time() + 300
        ok = False
        while time.time() < deadline:
            try:
                if (any(f in page.url for f in LOGIN_SUCCESS_FRAGMENTS)
                        or _has_login_cookies(ctx)):
                    # 等待 SSO cookie 全部落盘（登录后还会补写若干 cookie）
                    time.sleep(3)
                    ctx.storage_state(path=str(state))
                    print(f"登录成功，会话已保存: {state}")
                    ok = True
                    break
            except Exception:
                # 页面跳转/导航瞬间访问 page.url 可能抛错，稍后重试
                pass
            time.sleep(1)
        if not ok:
            print("超时未完成登录（或浏览器被手动关闭），未保存会话。")
        try:
            ctx.close()
            browser.close()
        except Exception:
            pass  # 浏览器已被用户手动关闭
        return 0 if ok else 1


def _cli_check() -> int:
    """检查已保存的登录态是否有效。"""
    from playwright.sync_api import sync_playwright

    state = _state_path()
    if not state.exists():
        print(f"未找到会话文件 {state}，请先执行: "
              "python -m news_crew.tools.toutiao_tool login")
        return 1
    with sync_playwright() as pw:
        browser, ctx = _launch(pw, headless=True)
        page = ctx.new_page()
        page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
        ok = _session_valid(page, ctx)
        print(f"登录态{'有效' if ok else '已过期'}（当前 URL: {page.url}）")
        if not ok:
            print("请重新执行: python -m news_crew.tools.toutiao_tool login")
        ctx.close()
        browser.close()
        return 0 if ok else 1


def _cli_list(keyword: str | None = None) -> int:
    """打开内容管理页，列出最近发布的文章（可按关键词过滤）。

    用途：发布结果为「结果未知」或需人工核实时，用本命令确认文章
    是否真的出现在头条号后台的内容管理列表中。
    """
    from playwright.sync_api import sync_playwright

    state = _state_path()
    if not state.exists():
        print(f"未找到会话文件 {state}，请先执行: "
              "python -m news_crew.tools.toutiao_tool login")
        return 1
    with sync_playwright() as pw:
        browser, ctx = _launch(pw, headless=True)
        page = ctx.new_page()
        page.goto(MANAGE_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass  # 长连接导致 networkidle 超时，容忍
        time.sleep(3)
        if not _session_valid(page, ctx):
            print("登录态已过期，请重新执行: "
                  "python -m news_crew.tools.toutiao_tool login")
            ctx.close()
            browser.close()
            return 1
        text = page.evaluate(
            "() => document.body ? (document.body.innerText || '') : ''")
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if keyword:
            hits = [l for l in lines if keyword in l]
            print(f"内容管理页中包含「{keyword}」的行（共 {len(hits)} 条）：")
            for l in hits[:20]:
                print("  -", l)
            if not hits:
                print("  （无匹配，可能文章未发布成功或仍在审核中）")
        else:
            print("内容管理页文本（前 60 行）：")
            for l in lines[:60]:
                print("  ", l)
        ctx.close()
        browser.close()
        return 0


if __name__ == "__main__":
    import sys

    _cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if _cmd == "login":
        sys.exit(_cli_login())
    elif _cmd == "check":
        sys.exit(_cli_check())
    elif _cmd == "publish" and len(sys.argv) == 4:
        _title, _file = sys.argv[2], sys.argv[3]
        _content = Path(_file).read_text(encoding="utf-8")
        print(_publish_article(_title, _content))
    elif _cmd == "list":
        sys.exit(_cli_list(sys.argv[2] if len(sys.argv) > 2 else None))
    else:
        print(__doc__)
