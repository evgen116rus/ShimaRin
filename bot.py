import asyncio
import html
import json
import logging
import re
from urllib.parse import urljoin

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
)

logging.basicConfig(level=logging.INFO)

# ===== НАСТРОЙКИ: подставь свои значения =====
TOKEN = "9156288158:AAFuHVZHRjCgobeO2ySZvyZsKO0vXVhgH6A"
CHANNEL_ID = -1002188730333
ADMINS = {105011935, 1951524388, 5594255687, 6978093886, 418379762}
# =============================================

SETTINGS_FILE = "settings.json"
ALBUM_WAIT = 1.5
MAX_UPLOAD = 50 * 1024 * 1024  # лимит Telegram на загрузку файла ботом
CAPTION_LIMIT = 1024           # лимит подписи к медиа

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

albums = {}  # media_group_id -> [(kind, file_id), ...]


# ---------- подпись ----------
def load_caption() -> str:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f).get("caption", "")
    except FileNotFoundError:
        return ""


def save_caption(text: str) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({"caption": text}, f, ensure_ascii=False)


def is_admin(m: Message) -> bool:
    return not ADMINS or (m.from_user is not None and m.from_user.id in ADMINS)


# ---------- команды ----------
@dp.message(Command("start"))
async def start(m: Message):
    if not is_admin(m):
        return
    await m.answer(
        "Пришли или перешли мне картинки, видео, GIF, пост из расширенного "
        "редактора или ссылку на пост, я опубликую их в канал без источника.\n\n"
        "/caption текст - задать готовую подпись (ссылки сохраняются)\n"
        "/nocaption - без подписи\n"
        "/status - текущие настройки\n"
        "/test - проверка доступа к каналу"
    )


# Markdown-ссылка [текст](https://адрес) -> HTML-ссылка
MD_LINK_RE = re.compile(
    r'\[([^\]]+)\]\((?:<a href="[^"]*">)?(https?://[^)\s<]+)(?:</a>)?\)'
)
# Сущности, которые Telegram находит сам; это не форматирование от пользователя
AUTO_ENTITIES = {
    "bot_command", "url", "mention", "hashtag",
    "cashtag", "email", "phone_number",
}
HTML_TAG_RE = re.compile(
    r"</?(a|b|i|u|s|em|strong|code|pre|tg-spoiler|blockquote)\b", re.I
)


def build_caption(m: Message, command: CommandObject) -> str:
    has_formatting = any(
        e.type not in AUTO_ENTITIES for e in (m.entities or [])
    )
    if has_formatting:
        # форматирование из меню Telegram (ссылка, жирный, курсив...)
        parts = m.html_text.split(None, 1)
        caption = parts[1] if len(parts) > 1 else ""
    else:
        raw = command.args or ""
        # если пользователь сам написал HTML-теги, оставляем как есть
        caption = raw if HTML_TAG_RE.search(raw) else html.escape(raw, quote=False)
    # [текст](ссылка) в стиле Markdown -> настоящая ссылка
    return MD_LINK_RE.sub(r'<a href="\2">\1</a>', caption).strip()


@dp.message(Command("caption"))
async def set_caption(m: Message, command: CommandObject):
    if not is_admin(m):
        return
    if not command.args:
        await m.answer("Использование: /caption текст подписи")
        return
    caption = build_caption(m, command)
    if not caption:
        await m.answer("Подпись получилась пустой.")
        return
    try:
        await m.answer(
            caption,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        await m.answer(f"❌ Не удалось разобрать подпись: {e}")
        return
    save_caption(caption)
    note = ""
    if len(re.sub(r"<[^>]+>", "", caption)) > CAPTION_LIMIT:
        note = f"\n⚠️ Подпись длиннее {CAPTION_LIMIT} символов, Telegram не примет её к медиа."
    await m.answer("Подпись сохранена ✅ Так она будет выглядеть (выше)." + note)


@dp.message(Command("nocaption"))
async def no_caption(m: Message):
    if not is_admin(m):
        return
    save_caption("")
    await m.answer("Теперь публикую без подписи ✅")


@dp.message(Command("status"))
async def status(m: Message):
    if not is_admin(m):
        return
    cap = load_caption()
    await m.answer(
        f"Канал: {CHANNEL_ID}\nПодпись:\n{cap or '- нет -'}",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@dp.message(Command("test"))
async def test(m: Message):
    if not is_admin(m):
        return
    try:
        chat = await bot.get_chat(CHANNEL_ID)
        me = await bot.get_chat_member(CHANNEL_ID, bot.id)
        await m.answer(f"Канал: {chat.title}\nСтатус бота: {me.status}")
    except Exception as e:
        await m.answer(f"❌ {e}")


# ---------- альбомы ----------
def make_media(kind: str, media, caption):
    if kind == "video":
        return InputMediaVideo(media=media, caption=caption)
    return InputMediaPhoto(media=media, caption=caption)


def queue_album(m: Message, kind: str, file_id: str):
    gid = m.media_group_id
    if gid not in albums:
        albums[gid] = []
        asyncio.create_task(send_album(gid, m))
    albums[gid].append((kind, file_id))


async def send_album(group_id: str, reply_to: Message):
    await asyncio.sleep(ALBUM_WAIT)
    items = albums.pop(group_id, [])
    caption = load_caption() or None
    try:
        for i in range(0, len(items), 10):  # максимум 10 в альбоме
            chunk = items[i:i + 10]
            media = [
                make_media(kind, fid, caption if (i == 0 and j == 0) else None)
                for j, (kind, fid) in enumerate(chunk)
            ]
            await bot.send_media_group(CHANNEL_ID, media)
        await reply_to.answer(f"✅ Опубликовано файлов: {len(items)}")
    except Exception as e:
        logging.exception("album send failed")
        await reply_to.answer(f"❌ Ошибка: {e}")


# ---------- фото / видео / GIF / файлы ----------
@dp.message(F.photo)
async def on_photo(m: Message):
    if not is_admin(m):
        return
    file_id = m.photo[-1].file_id
    if m.media_group_id:
        queue_album(m, "photo", file_id)
        return
    try:
        await bot.send_photo(CHANNEL_ID, file_id, caption=load_caption() or None)
        await m.answer("✅ Опубликовано")
    except Exception as e:
        logging.exception("send failed")
        await m.answer(f"❌ Ошибка: {e}")


@dp.message(F.video)
async def on_video(m: Message):
    if not is_admin(m):
        return
    file_id = m.video.file_id
    if m.media_group_id:
        queue_album(m, "video", file_id)
        return
    try:
        await bot.send_video(CHANNEL_ID, file_id, caption=load_caption() or None)
        await m.answer("✅ Опубликовано")
    except Exception as e:
        logging.exception("send failed")
        await m.answer(f"❌ Ошибка: {e}")


@dp.message(F.animation)
async def on_animation(m: Message):
    # GIF (Telegram хранит их как зацикленные mp4)
    if not is_admin(m):
        return
    try:
        await bot.send_animation(
            CHANNEL_ID, m.animation.file_id, caption=load_caption() or None
        )
        await m.answer("✅ Опубликовано")
    except Exception as e:
        logging.exception("send failed")
        await m.answer(f"❌ Ошибка: {e}")


@dp.message(F.document & F.document.mime_type.startswith("image/"))
async def on_image_document(m: Message):
    if not is_admin(m):
        return
    try:
        await bot.send_document(
            CHANNEL_ID, m.document.file_id, caption=load_caption() or None
        )
        await m.answer("✅ Опубликовано")
    except Exception as e:
        logging.exception("send failed")
        await m.answer(f"❌ Ошибка: {e}")


@dp.message(F.document & F.document.mime_type.startswith("video/"))
async def on_video_document(m: Message):
    if not is_admin(m):
        return
    try:
        await bot.send_document(
            CHANNEL_ID, m.document.file_id, caption=load_caption() or None
        )
        await m.answer("✅ Опубликовано")
    except Exception as e:
        logging.exception("send failed")
        await m.answer(f"❌ Ошибка: {e}")


# ---------- посты из расширенного редактора (Rich Message) ----------
def get_rich(m: Message):
    rm = getattr(m, "rich_message", None) or (m.model_extra or {}).get("rich_message")
    if rm is None:
        return None
    if hasattr(rm, "model_dump"):
        return rm.model_dump(mode="json", exclude_none=True)
    return rm


def collect_media(node, out: list):
    """Рекурсивно собирает фото, видео и GIF из блоков (в т.ч. вложенных)."""
    if isinstance(node, dict):
        t = node.get("type")
        if t == "photo" and isinstance(node.get("photo"), list) and node["photo"]:
            out.append(("photo", node["photo"][-1]["file_id"]))
            return
        if t in ("video", "animation"):
            obj = node.get(t)
            if isinstance(obj, dict) and obj.get("file_id"):
                out.append((t, obj["file_id"]))
                return
        for v in node.values():
            collect_media(v, out)
    elif isinstance(node, list):
        for v in node:
            collect_media(v, out)


def collect_types(node, found: set):
    if isinstance(node, dict):
        if isinstance(node.get("type"), str):
            found.add(node["type"])
        for v in node.values():
            collect_types(v, found)
    elif isinstance(node, list):
        for v in node:
            collect_types(v, found)


async def publish_items(items: list, caption):
    media_items = [(k, f) for k, f in items if k in ("photo", "video")]
    animations = [f for k, f in items if k == "animation"]
    first = True

    for i in range(0, len(media_items), 10):
        chunk = media_items[i:i + 10]
        if len(chunk) == 1:
            kind, fid = chunk[0]
            cap = caption if first else None
            if kind == "photo":
                await bot.send_photo(CHANNEL_ID, fid, caption=cap)
            else:
                await bot.send_video(CHANNEL_ID, fid, caption=cap)
        else:
            media = [
                make_media(k, f, caption if (first and j == 0) else None)
                for j, (k, f) in enumerate(chunk)
            ]
            await bot.send_media_group(CHANNEL_ID, media)
        first = False

    for fid in animations:
        await bot.send_animation(
            CHANNEL_ID, fid, caption=caption if first else None
        )
        first = False


@dp.message(lambda m: get_rich(m) is not None)
async def on_rich(m: Message):
    if not is_admin(m):
        return
    rich = get_rich(m)
    items = []
    collect_media(rich, items)
    if not items:
        types = set()
        collect_types(rich, types)
        logging.info("rich message without media: %s", rich)
        await m.answer(
            "❌ В этом посте не нашёл картинок. Типы блоков: "
            + (", ".join(sorted(types)) or "нет")
        )
        return
    try:
        await publish_items(items, load_caption() or None)
        await m.answer(f"✅ Опубликовано файлов: {len(items)}")
    except Exception as e:
        logging.exception("rich repost failed")
        await m.answer(f"❌ Ошибка: {e}")


# ---------- отправка скачанных файлов ----------
async def send_single(kind: str, data: bytes, caption):
    if kind == "photo":
        await bot.send_photo(
            CHANNEL_ID, BufferedInputFile(data, "image.jpg"), caption=caption
        )
    elif kind == "animation":
        await bot.send_animation(
            CHANNEL_ID, BufferedInputFile(data, "anim.mp4"), caption=caption
        )
    else:
        await bot.send_video(
            CHANNEL_ID,
            BufferedInputFile(data, "video.mp4"),
            caption=caption,
            supports_streaming=True,
        )


# ---------- ссылки t.me/канал/номер ----------
LINK_RE = re.compile(
    r"https?://(?:www\.)?t\.me/(?:c/(\d+)|([A-Za-z0-9_]{4,}))/(\d+)"
)
MEDIA_RE = re.compile(
    r"tgme_widget_message_photo_wrap[^>]*?background-image:url\('([^']+)'\)"
    r"|(<video[^>]*>)"
)
SRC_RE = re.compile(r'\ssrc="([^"]+)"')


async def fetch_media(username: str, msg_id: str):
    """Возвращает [(kind, bytes)], kind: photo / video / animation."""
    url = f"https://t.me/{username}/{msg_id}?embed=1&mode=tme"
    timeout = aiohttp.ClientTimeout(total=120)
    headers = {"User-Agent": "Mozilla/5.0"}
    result = []
    seen = set()
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as s:
        async with s.get(url) as r:
            if r.status != 200:
                return []
            page = await r.text()
        for mt in MEDIA_RE.finditer(page):
            photo, tag = mt.groups()
            if photo:
                kind, src = "photo", photo
            else:
                sm = SRC_RE.search(tag)
                if not sm:
                    continue
                src = sm.group(1)
                kind = "animation" if " loop" in tag else "video"
            src = html.unescape(src)
            if src in seen:
                continue
            seen.add(src)
            async with s.get(src) as r:
                if r.status != 200:
                    continue
                data = await r.read()
            if len(data) > MAX_UPLOAD:
                raise ValueError("файл больше 50 МБ")
            result.append((kind, data))
    return result


async def repost_link(priv_id, username, msg_id, caption):
    # 1) публичный канал: скачиваем файлы и отправляем заново
    if username:
        try:
            items = await fetch_media(username, msg_id)
        except Exception:
            logging.exception("fetch_media failed, пробую copy_message")
            items = []

        if len(items) == 1:
            await send_single(items[0][0], items[0][1], caption)
            return

        if len(items) > 1:
            if all(k in ("photo", "video") for k, _ in items):
                media = [
                    make_media(
                        k,
                        BufferedInputFile(
                            b, f"{i}.{'jpg' if k == 'photo' else 'mp4'}"
                        ),
                        caption if i == 0 else None,
                    )
                    for i, (k, b) in enumerate(items[:10])
                ]
                await bot.send_media_group(CHANNEL_ID, media)
            else:  # есть GIF: в альбом их класть нельзя, шлём по одному
                for i, (k, b) in enumerate(items):
                    await send_single(k, b, caption if i == 0 else None)
            return

    # 2) запасной вариант: copy_message (без пометки "переслано")
    from_chat = f"@{username}" if username else int(f"-100{priv_id}")
    await bot.copy_message(
        CHANNEL_ID, from_chat, int(msg_id), caption=caption or ""
    )


@dp.message(F.text.func(lambda t: bool(LINK_RE.search(t))))
async def on_link(m: Message):
    if not is_admin(m):
        return
    caption = load_caption() or None
    done = 0
    for priv_id, username, msg_id in LINK_RE.findall(m.text):
        try:
            await repost_link(priv_id, username, msg_id, caption)
            done += 1
        except Exception as e:
            logging.exception("link repost failed")
            await m.answer(f"❌ {username or priv_id}/{msg_id}: {e}")
    if done:
        await m.answer(f"✅ Опубликовано постов: {done}")


# ---------- сообщения со ссылкой-превью ----------
META_RE = re.compile(r"<meta\s[^>]*>", re.I)


def get_preview_url(m: Message):
    lpo = m.link_preview_options
    if lpo and lpo.url:
        return lpo.url
    for ent in (m.entities or []):
        if ent.type == "text_link" and ent.url:
            return ent.url
        if ent.type == "url":
            return ent.extract_from(m.text)
    return None


async def download(session, link):
    async with session.get(link) as r:
        if r.status != 200:
            return None, None
        ctype = r.headers.get("Content-Type", "").lower()
        data = await r.read()
    if len(data) > MAX_UPLOAD:
        raise ValueError("файл больше 50 МБ")
    if ctype.startswith("image/gif"):
        return "animation", data
    if ctype.startswith("image/"):
        return "photo", data
    if ctype.startswith("video/"):
        return "video", data
    return None, None


async def fetch_remote(url: str):
    """Ссылка на файл или на страницу с og:image / og:video."""
    timeout = aiohttp.ClientTimeout(total=60)
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as s:
        # 1) ссылка сразу на файл
        async with s.get(url) as r:
            if r.status != 200:
                return None, None
            ctype = r.headers.get("Content-Type", "").lower()
            page = await r.text(errors="ignore") if "html" in ctype else None
        if page is None:
            return await download(s, url)

        # 2) страница: ищем og-теги
        img = vid = None
        for tag in META_RE.findall(page):
            prop = re.search(r'(?:property|name)="og:(image|video)"', tag, re.I)
            cont = re.search(r'content="([^"]+)"', tag, re.I)
            if not prop or not cont:
                continue
            link = urljoin(url, html.unescape(cont.group(1)))
            if prop.group(1).lower() == "video" and not vid:
                vid = link
            elif prop.group(1).lower() == "image" and not img:
                img = link
        link = vid or img
        if not link:
            return None, None
        return await download(s, link)


@dp.message(F.text, lambda m: bool(get_preview_url(m)))
async def on_text_with_preview(m: Message):
    if not is_admin(m):
        return
    url = get_preview_url(m)
    try:
        kind, data = await fetch_remote(url)
        if not data:
            await m.answer(f"❌ Не нашёл картинку или видео по ссылке: {url}")
            return
        await send_single(kind, data, load_caption() or None)
        await m.answer("✅ Опубликовано")
    except Exception as e:
        logging.exception("preview repost failed")
        await m.answer(f"❌ Ошибка: {e}")


# ---------- всё остальное (должно быть последним) ----------
@dp.message()
async def fallback(m: Message):
    uid = m.from_user.id if m.from_user else None
    if not is_admin(m):
        await m.answer(f"⛔ Нет доступа. Твой ID: {uid}")
        return
    await m.answer(
        f"Не знаю, как опубликовать это сообщение (тип: {m.content_type}). "
        "Поддерживаются фото, видео, GIF, файлы, посты из расширенного "
        "редактора и ссылки."
    )


# ---------- запуск ----------
async def main():
    await bot.set_my_commands([
        BotCommand(command="start", description="Справка"),
        BotCommand(command="caption", description="Задать подпись"),
        BotCommand(command="nocaption", description="Без подписи"),
        BotCommand(command="status", description="Текущие настройки"),
        BotCommand(command="test", description="Проверить доступ к каналу"),
    ])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())