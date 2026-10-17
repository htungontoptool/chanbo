import os
import io
import re
import asyncio
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---- Logging (Railway xem log ở tab Deployments) ----
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# Railway inject biến môi trường
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Thiếu biến môi trường BOT_TOKEN")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36"
}
TIMEOUT = 25


def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text


def is_url(text: str) -> bool:
    try:
        p = urlparse(text)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def fix_css_urls(css: str, css_url: str) -> str:
    def repl(m):
        raw = m.group(1).strip().strip('"').strip("'")
        if raw.startswith(("http", "data:", "//")):
            return f"url({raw})"
        return f"url({urljoin(css_url, raw)})"
    return re.sub(r"url\(([^)]+)\)", repl, css)


def inline_css(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    collected_css = []

    # Tải CSS từ <link rel=stylesheet>
    for link in soup.find_all("link", rel=lambda v: v and "stylesheet" in v):
        href = link.get("href")
        if href:
            css_url = urljoin(base_url, href)
            try:
                css = fetch(css_url)
                css = fix_css_urls(css, css_url)
                collected_css.append(f"/* ==== {css_url} ==== */\n{css}")
            except Exception as e:
                collected_css.append(f"/* Lỗi tải {css_url}: {e} */")
        link.decompose()

    # Gom <style> cũ
    for style in soup.find_all("style"):
        collected_css.append(style.get_text())
        style.decompose()

    # Chèn 1 <style> gộp vào <head>
    if collected_css:
        new_style = soup.new_tag("style")
        new_style.string = "\n\n".join(collected_css)
        if not soup.head:
            if soup.html:
                soup.html.insert(0, soup.new_tag("head"))
            else:
                soup.insert(0, soup.new_tag("head"))
        soup.head.append(new_style)

    # Tuyệt đối hoá link tương đối
    for tag, attr in [("img", "src"), ("script", "src"),
                      ("a", "href"), ("source", "src"),
                      ("link", "href")]:
        for t in soup.find_all(tag):
            v = t.get(attr)
            if v and not v.startswith(("http", "data:", "#", "mailto:", "javascript:", "//")):
                t[attr] = urljoin(base_url, v)

    return str(soup)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Gửi URL (http/https), tôi sẽ tải HTML + CSS và trả file .html đã gộp."
    )


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not is_url(text):
        await update.message.reply_text("❌ URL không hợp lệ.")
        return

    msg = await update.message.reply_text("⏳ Đang tải...")
    loop = asyncio.get_event_loop()
    try:
        html = await loop.run_in_executor(None, fetch, text)
        final_html = await loop.run_in_executor(None, inline_css, html, text)
    except Exception as e:
        log.exception("Lỗi xử lý %s", text)
        await msg.edit_text(f"❌ Lỗi: {e}")
        return

    buf = io.BytesIO(final_html.encode("utf-8"))
    domain = urlparse(text).netloc.replace(":", "_")
    filename = f"{domain}.html"
    buf.name = filename

    await msg.edit_text("📤 Đang gửi file...")
    await update.message.reply_document(
        document=buf,
        filename=filename,
        caption=f"✅ {text}\nKích thước: {len(final_html)/1024:.1f} KB",
    )
    await msg.delete()


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Bot khởi động...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()