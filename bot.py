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

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("@kavaikiti")  # например -1002188730333 или @channel
ADMINS = {int(x) for x in os.getenv("105011935", "1951524388", "5594255687").split(",") if x.strip()}
SETTINGS_FILE = "settings.json"
ALBUM_WAIT = 1.5

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

albums = {}


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


@dp.message(Command("start"))
async def start(m: Message):
    if not is_admin(m):
        return
    await m.answer(
        "Пришли или перешли мне картинки, я опубликую их в канал без источника.\n\n"
        "/caption текст - задать готовую подпись\n"
        "/nocaption - без подписи\n"
        "/status - текущие настройки\n"
        "/test - проверка доступа к каналу"
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
    await m.answer(f"Канал: {CHANNEL_ID}\nПодпись: {cap or '- нет -'}")


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


async def send_album(group_id: str, reply_to: Message):
    await asyncio.sleep(ALBUM_WAIT)
    file_ids = albums.pop(group_id, [])
    caption = load_caption() or None
    try:
        for i in range(0, len(file_ids), 10):
            chunk = file_ids[i:i + 10]
            media = [
                InputMediaPhoto(
                    media=fid,
                    caption=caption if (i == 0 and j == 0) else None,
                )
                for j, fid in enumerate(chunk)
            ]
            await bot.send_media_group(CHANNEL_ID, media)
        await reply_to.answer(f"✅ Опубликовано картинок: {len(file_ids)}")
    except Exception as e:
        logging.exception("album send failed")
        await reply_to.answer(f"❌ Ошибка: {e}")


@dp.message(F.photo)
async def on_photo(m: Message):
    if not is_admin(m):
        return
    file_id = m.photo[-1].file_id

    if m.media_group_id:
        gid = m.media_group_id
        if gid not in albums:
            albums[gid] = []
            asyncio.create_task(send_album(gid, m))
        albums[gid].append(file_id)
        return

    try:
        await bot.send_photo(CHANNEL_ID, file_id, caption=load_caption() or None)
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


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
