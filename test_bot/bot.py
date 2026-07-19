# bot.py
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from models.database import init_db
from handlers import admin, lottery, account

logging.basicConfig(level=logging.INFO)


async def main():
    init_db()
    
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    dp = Dispatcher()
    
    dp.include_routers(admin.router, lottery.router, account.router)
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
