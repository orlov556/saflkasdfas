import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Импорт хендлеров лотереи
from handlers.lottery import router as lottery_router
from services.lottery import LotteryService
from models.lottery import Lottery, Ticket, LotteryWinner
from database import init_db, get_session

# Подключаем роутеры
dp.include_router(lottery_router)

# Обработчик команды /start
@dp.message(Command("start"))
async def start_cmd(message: Message):
    await message.answer(
        "🎰 **Тестовый бот для лотереи**\n\n"
        "Доступные команды:\n"
        "• /new_lot — создать лотерею\n"
        "• /create_lottery — создать лотерею\n"
        "• /add_channel @username — добавить канал\n"
        "• /channels — список каналов\n"
        "• /test_ticket — тест выбора билета\n"
        "• /stats — статистика\n\n"
        "📌 Сначала добавь канал командой /add_channel"
    )

# Обработчик добавления канала
@dp.message(Command("add_channel"))
async def add_channel_cmd(message: Message, session):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Использование: /add_channel @username")
        return
    
    channel_username = args[1].strip()
    try:
        chat = await bot.get_chat(channel_username)
        # Сохраняем в БД
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
async def channels_cmd(message: Message, session):
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
async def stats_cmd(message: Message, session):
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

# Тестовый выбор билета
@dp.message(Command("test_ticket"))
async def test_ticket_cmd(message: Message, session):
    from sqlalchemy import select
    
    # Находим активную лотерею
    lottery = await session.execute(
        select(Lottery).where(Lottery.is_active == True).limit(1)
    )
    lottery = lottery.scalar_one_or_none()
    
    if not lottery:
        await message.answer("❌ Нет активной лотереи. Создай через /new_lot")
        return
    
    # Показываем информацию
    await message.answer(
        f"🎰 **Тестовая лотерея**\n\n"
        f"ID: {lottery.id}\n"
        f"Билетов: {lottery.total_tickets}\n"
        f"Выигрышные: {', '.join(map(str, lottery.winning_numbers))}\n"
        f"Статус: {'Активна' if lottery.is_active else 'Завершена'}\n\n"
        f"💡 Нажми на любой билет в канале, чтобы проверить."
    )

async def main():
    # Инициализация БД
    await init_db()
    
    # Запуск бота
    print("🤖 Тестовый бот запущен!")
    print("📌 Команды:")
    print("  /start - приветствие")
    print("  /add_channel @username - добавить канал")
    print("  /channels - список каналов")
    print("  /new_lot - создать лотерею")
    print("  /stats - статистика")
    print("  /test_ticket - тест билета")
    
    await dp.start_polling(bot)

if name == "__main__":
    asyncio.run(main())