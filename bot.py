import asyncio
import json
import os
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, InputMediaPhoto

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("")
CHANNEL_ID = os.getenv("2188730333")  # @mychannel или -1001234567890
ADMINS = {int(x) for x in os.getenv("105011935", "1951524388", "5594255687").split(",") if x.strip()}
SETTINGS_FILE = "settings.json"
ALBUM_WAIT = 1.5  # сек. ожидания остальных картинок альбома

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

albums: dict[str, list[str]] = {}


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
    return not ADMINS or (m.from_user and m.from_user.id in ADMINS)


@dp.message(Command("start"))
async def start(m: Message):
    if not is_admin(m):
        return
    await m.answer(
        "Пришли или перешли мне картинки — я опубликую их в канал без источника и подписи.\n\n"
        "/caption текст — задать готовую подпись (поддерживается HTML)\n"
        "/nocaption — публиковать без подписи\n"
        "/status — текущая подпись"
    )


@dp.message(Command("caption"))
async def set_caption(m: Message, command: CommandObject):
    if not is_admin(m):
        return
    if not command.args:
        await m.answer("Использование: /caption текст подписи")
        return
    save_caption(command.args)
    await m.answer("Подпись сохранена ✅")


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
    await m.answer(f"Канал: {CHANNEL_ID}\nПодпись: {cap or '— нет —'}")


async def send_album(group_id: str):
    await asyncio.sleep(ALBUM_WAIT)
    file_ids = albums.pop(group_id, [])
    caption = load_caption() or None
    for i in range(0, len(file_ids), 10):  # лимит Telegram — 10 в альбоме
        chunk = file_ids[i:i + 10]
        media = [
            InputMediaPhoto(media=fid, caption=caption if (i == 0 and j == 0) else None)
            for j, fid in enumerate(chunk)
        ]
        await bot.send_media_group(CHANNEL_ID, media)


@dp.message(F.photo)
async def on_photo(m: Message):
    if not is_admin(m):
        return
    file_id = m.photo[-1].file_id  # самый большой размер

    if m.media_group_id:
        gid = m.media_group_id
        if gid not in albums:
            albums[gid] = []
            asyncio.create_task(send_album(gid))
        albums[gid].append(file_id)
        return

    await bot.send_photo(CHANNEL_ID, file_id, caption=load_caption() or None)
    await m.react([]) if False else None


@dp.message(F.document & F.document.mime_type.startswith("image/"))
async def on_image_document(m: Message):
    # картинки, присланные «как файл» (без сжатия)
    if not is_admin(m):
        return
    await bot.send_document(CHANNEL_ID, m.document.file_id, caption=load_caption() or None)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
