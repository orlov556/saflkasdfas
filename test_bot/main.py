import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN
from database import init_db
from middlewares.db import DbSessionMiddleware

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Подключаем middleware для БД
dp.update.middleware(DbSessionMiddleware())

# Импорт хендлеров
from handlers.lottery import router as lottery_router

# Подключаем роутеры
dp.include_router(lottery_router)

# Обработчик команды /start
from aiogram import types
from aiogram.filters import Command

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "🎰 **Тестовый бот для лотереи**\n\n"
        "Доступные команды:\n"
        "• /new_lot — создать лотерею\n"
        "• /add_channel @username — добавить канал\n"
        "• /channels — список каналов\n"
        "• /stats — статистика\n\n"
        "📌 Сначала добавь канал командой /add_channel"
    )

# Обработчик добавления канала
@dp.message(Command("add_channel"))
async def add_channel_cmd(message: types.Message, session):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Использование: /add_channel @username")
        return
    
    channel_username = args[1].strip()
    try:
        chat = await bot.get_chat(channel_username)
        from models.channel import Channel
        channel = Channel(
            chat_id=chat.id,
            title=chat.title,
            username=chat.username
        )
        session.add(channel)
        await session.commit()
        await message.answer(f"✅ Канал {chat.title} добавлен!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}\nУбедись, что бот админ в канале.")

# Обработчик списка каналов
@dp.message(Command("channels"))
async def channels_cmd(message: types.Message, session):
    from models.channel import Channel
    from sqlalchemy import select
    
    result = await session.execute(select(Channel))
    channels = result.scalars().all()
    
    if not channels:
        await message.answer("❌ Нет добавленных каналов.")
        return
    
    text = "📌 **Доступные каналы:**\n\n"
    for ch in channels:
        text += f"• {ch.title} (@{ch.username}) — ID: {ch.chat_id}\n"
    await message.answer(text)

# Обработчик статистики
@dp.message(Command("stats"))
async def stats_cmd(message: types.Message, session):
    from models.lottery import Lottery, Ticket, LotteryWinner
    from sqlalchemy import select, func
    
    lotteries_count = await session.scalar(select(func.count()).select_from(Lottery))
    tickets_count = await session.scalar(select(func.count()).select_from(Ticket))
    winners_count = await session.scalar(select(func.count()).select_from(LotteryWinner))
    
    await message.answer(
        f"📊 **Статистика тестового бота:**\n\n"
        f"🎰 Лотерей: {lotteries_count or 0}\n"
        f"🎟️ Билетов: {tickets_count or 0}\n"
        f"🏆 Победителей: {winners_count or 0}"
    )

async def main():
    # Удаляем вебхук
    await bot.delete_webhook(drop_pending_updates=True)
    print("✅ Webhook удалён, запускаю polling...")
    
    # Инициализация БД
    await init_db()
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
