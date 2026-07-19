import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
import logging
import random
import json
from datetime import datetime
from contextlib import contextmanager

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Boolean, DateTime, ForeignKey, Float
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ")
MIN_TICKETS = 5
MAX_TICKETS = 100
MAX_WINNERS = 100
CB_PREFIX = "lot"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ================= БАЗА ДАННЫХ =================
Base = declarative_base()
engine = create_engine("sqlite:///lottery.db", echo=False)
SessionLocal = sessionmaker(bind=engine)

@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"DB Error: {e}")
        raise
    finally:
        session.close()

def init_db():
    Base.metadata.create_all(engine)


class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)
    username = Column(String)
    first_name = Column(String)
    balance_stars = Column(Float, default=0.0)
    total_spent = Column(Float, default=0.0)
    total_won = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    tickets = relationship("Ticket", back_populates="user")
    organized_lotteries = relationship("Lottery", back_populates="organizer")


class Lottery(Base):
    __tablename__ = "lotteries"
    id = Column(Integer, primary_key=True)
    message_id = Column(BigInteger, nullable=True)
    chat_id = Column(String, nullable=True) # String для поддержки @username и -100...
    organizer_id = Column(BigInteger, ForeignKey("users.id"))
    title = Column(String)
    total_tickets = Column(Integer)
    winners_count = Column(Integer, default=1)
    price_stars = Column(Float, default=0.0)
    winning_numbers = Column(String)
    require_premium = Column(Boolean, default=False)
    require_boost = Column(Boolean, default=False)
    require_subscription = Column(Boolean, default=False)
    subscription_chat_id = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    organizer = relationship("User", back_populates="organized_lotteries")
    tickets = relationship("Ticket", back_populates="lottery")


class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True)
    lottery_id = Column(Integer, ForeignKey("lotteries.id"))
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    number = Column(Integer)
    is_winner = Column(Boolean, default=False)
    is_picked = Column(Boolean, default=False)
    picked_at = Column(DateTime, nullable=True)
    lottery = relationship("Lottery", back_populates="tickets")
    user = relationship("User", back_populates="tickets")


class Withdrawal(Base):
    __tablename__ = "withdrawals"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    amount = Column(Float)
    status = Column(String, default="pending") # pending, approved, rejected
    created_at = Column(DateTime, default=datetime.utcnow)


# ================= FSM =================
class CreateLottery(StatesGroup):
    title = State()
    tickets_count = State()
    winners_count = State()
    price = State()
    conditions = State()
    sub_channel = State()      # Если нужна подписка
    publish_channel = State()  # Канал для публикации


# ================= РОУТЕРЫ =================
admin_router = Router()
lottery_router = Router()
account_router = Router()


@admin_router.message(Command("newlottery"))
async def cmd_new_lottery(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    await state.set_state(CreateLottery.title)
    await message.answer("<b>🎯 Создание лотереи</b>\n\nВведи название розыгрыша:")


@admin_router.message(CreateLottery.title)
async def process_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(CreateLottery.tickets_count)
    await message.answer(f"Название: <b>{message.text}</b>\n\nСколько билетов? (от {MIN_TICKETS} до {MAX_TICKETS})")


@admin_router.message(CreateLottery.tickets_count)
async def process_tickets(message: Message, state: FSMContext):
    try:
        count = int(message.text)
        if not (MIN_TICKETS <= count <= MAX_TICKETS):
            raise ValueError
    except ValueError:
        await message.answer(f"⚠️ Введи число от {MIN_TICKETS} до {MAX_TICKETS}")
        return
    await state.update_data(tickets_count=count)
    await state.set_state(CreateLottery.winners_count)
    await message.answer(f"Билетов: <b>{count}</b>\n\nСколько победителей? (от 1 до {min(count, MAX_WINNERS)})")


@admin_router.message(CreateLottery.winners_count)
async def process_winners(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        count = int(message.text)
        if not (1 <= count <= min(data['tickets_count'], MAX_WINNERS)):
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Некорректное количество победителей")
        return
    await state.update_data(winners_count=count)
    await state.set_state(CreateLottery.price)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆓 Бесплатно", callback_data=f"{CB_PREFIX}:price:0")],
        [InlineKeyboardButton(text="⭐ 1 Star", callback_data=f"{CB_PREFIX}:price:1")],
        [InlineKeyboardButton(text="⭐ 5 Stars", callback_data=f"{CB_PREFIX}:price:5")],
        [InlineKeyboardButton(text="⭐ 10 Stars", callback_data=f"{CB_PREFIX}:price:10")]
    ])
    await message.answer("Выбери стоимость билета:", reply_markup=kb)


@admin_router.callback_query(F.data.startswith(f"{CB_PREFIX}:price:"))
async def process_price(callback: CallbackQuery, state: FSMContext):
    price = float(callback.data.split(":")[-1])
    await state.update_data(price=price)
    await state.set_state(CreateLottery.conditions)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Premium", callback_data=f"{CB_PREFIX}:cond:premium")],
        [InlineKeyboardButton(text="🚀 Boost", callback_data=f"{CB_PREFIX}:cond:boost")],
        [InlineKeyboardButton(text="📢 Подписка на канал", callback_data=f"{CB_PREFIX}:cond:sub")],
        [InlineKeyboardButton(text="✅ Пропустить", callback_data=f"{CB_PREFIX}:cond:skip")]
    ])
    await callback.message.edit_text("Дополнительные условия участия:", reply_markup=kb)


@admin_router.callback_query(F.data.startswith(f"{CB_PREFIX}:cond:"))
async def process_conditions(callback: CallbackQuery, state: FSMContext):
    cond = callback.data.split(":")[-1]
    data = await state.get_data()
    
    conditions = {'premium': False, 'boost': False, 'subscription': False, 'sub_chat_id': None}
    
    if cond == "premium":
        conditions['premium'] = True
    elif cond == "boost":
        conditions['boost'] = True
    elif cond == "sub":
        conditions['subscription'] = True
        await state.update_data(conditions=conditions)
        await state.set_state(CreateLottery.sub_channel)
        await callback.message.edit_text("Введите username канала для проверки подписки (например, @durov):")
        return
    elif cond == "skip":
        pass
        
    await state.update_data(conditions=conditions)
    await state.set_state(CreateLottery.publish_channel)
    await callback.message.edit_text("Введите username канала, куда бот опубликует лотерею (например, @mychannel).\n⚠️ Бот должен быть там администратором!")


@admin_router.message(CreateLottery.sub_channel)
async def process_sub_channel(message: Message, state: FSMContext):
    channel = message.text.strip()
    if not channel.startswith("@"):
        await message.answer("⚠️ Username должен начинаться с @")
        return
    
    # Проверка, что бот там есть
    try:
        bot_info = await message.bot.get_chat_member(channel, message.bot.id)
        if bot_info.status in ["left", "kicked"]:
            await message.answer("⚠️ Бот не является участником этого канала. Добавьте его!")
            return
    except TelegramBadRequest:
        await message.answer("⚠️ Канал не найден. Проверьте правильность username.")
        return

    data = await state.get_data()
    conditions = data.get('conditions', {})
    conditions['sub_chat_id'] = channel
    await state.update_data(conditions=conditions)
    await state.set_state(CreateLottery.publish_channel)
    await message.answer("Отлично! Теперь введите username канала, куда опубликовать пост с лотереей:")


@admin_router.message(CreateLottery.publish_channel)
async def process_publish_channel(message: Message, state: FSMContext, bot: Bot):
    channel = message.text.strip()
    try:
        chat = await bot.get_chat(channel)
    except TelegramBadRequest:
        await message.answer("⚠️ Канал для публикации не найден. Проверьте username.")
        return

    data = await state.get_data()
    conditions = data.get('conditions', {})
    
    total = data['tickets_count']
    winners = random.sample(range(1, total + 1), data['winners_count'])
    
    with get_session() as session:
        user = session.query(User).filter_by(id=message.from_user.id).first()
        if not user:
            user = User(id=message.from_user.id, username=message.from_user.username, first_name=message.from_user.first_name)
            session.add(user)
            session.flush()

        lottery = Lottery(
            organizer_id=message.from_user.id,
            title=data['title'],
            total_tickets=total,
            winners_count=data['winners_count'],
            price_stars=data['price'],
            winning_numbers=json.dumps(winners),
            require_premium=conditions.get('premium', False),
            require_boost=conditions.get('boost', False),
            require_subscription=conditions.get('subscription', False),
            subscription_chat_id=conditions.get('sub_chat_id')
        )
        session.add(lottery)
        session.flush() # Получаем lottery.id

        for i in range(1, total + 1):
            session.add(Ticket(lottery_id=lottery.id, number=i))
        
        session.commit()
        lottery_id = lottery.id

    # Формируем кнопки
    buttons = []
    row = []
    for i in range(1, total + 1):
        row.append(InlineKeyboardButton(text=f"#{i}", callback_data=f"{CB_PREFIX}:ticket:{lottery_id}:{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    # ✅ ИСПРАВЛЕНИЕ: выносим логику цены в переменную, чтобы избежать \ внутри f-строки
    price_text = "Бесплатно" if data['price'] == 0 else f"{int(data['price'])} Stars"
    
    text = (
        f"🎉 <b>{data['title']}</b>\n\n"
        f"🎟 Билетов: <b>{total}</b>\n"
        f"🏆 Победителей: <b>{data['winners_count']}</b>\n"
        f"💰 Стоимость: <b>{price_text}</b>\n"
    )
    if conditions.get('premium'):
        text += "💎 Требуется <b>Premium</b>\n"
    if conditions.get('boost'):
        text += "🚀 Требуется <b>Boost</b>\n"
    if conditions.get('subscription'):
        text += f"📢 Требуется подписка на {conditions.get('sub_chat_id')}\n"
    text += "\n👇 <b>Выбери билет и испытай удачу!</b>"

    # Публикуем в канал
    try:
        sent_msg = await bot.send_message(chat_id=channel, text=text, reply_markup=kb)
        
        with get_session() as session:
            lottery = session.query(Lottery).filter_by(id=lottery_id).first()
            lottery.chat_id = str(sent_msg.chat.id)
            lottery.message_id = sent_msg.message_id
            session.commit()
            
        await state.clear()
        await message.answer(f"✅ Лотерея успешно создана и опубликована в {channel}!\nID лотереи: <code>{lottery_id}</code>")
    except TelegramForbiddenError:
        await message.answer("❌ Ошибка: Бот не имеет прав на публикацию сообщений в этом канале. Добавьте его в администраторы.")
    except Exception as e:
        logger.error(f"Publish error: {e}")
        await message.answer("❌ Произошла ошибка при публикации. Проверьте логи.")


# ================= УЧАСТИЕ В ЛОТЕРЕЕ =================
@lottery_router.callback_query(F.data.startswith(f"{CB_PREFIX}:ticket:"))
async def pick_ticket(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    lottery_id = int(parts[2])
    ticket_num = int(parts[3])

    with get_session() as session:
        lottery = session.query(Lottery).filter_by(id=lottery_id).first()
        if not lottery or not lottery.is_active:
            await callback.answer("🚫 Лотерея уже завершена", show_alert=True)
            return

        user = session.query(User).filter_by(id=callback.from_user.id).first()
        if not user:
            user = User(id=callback.from_user.id, username=callback.from_user.username, first_name=callback.from_user.first_name)
            session.add(user)
            session.flush()

        # 1. Проверка Premium
        if lottery.require_premium and not callback.from_user.is_premium:
            await callback.answer("⚠️ Для участия в этой лотерее нужен Telegram Premium!", show_alert=True)
            return

        # 2. Проверка подписки
        if lottery.require_subscription and lottery.subscription_chat_id:
            try:
                member = await bot.get_chat_member(lottery.subscription_chat_id, callback.from_user.id)
                if member.status in ["left", "kicked"]:
                    await callback.answer(f"⚠️ Подпишись на канал {lottery.subscription_chat_id}!", show_alert=True)
                    return
            except TelegramBadRequest:
                await callback.answer("⚠️ Ошибка проверки подписки. Возможно, бота удалили из канала.", show_alert=True)
                return

        # 3. Проверка, не брал ли уже билет
        existing = session.query(Ticket).filter_by(lottery_id=lottery_id, user_id=callback.from_user.id).first()
        if existing:
            await callback.answer("⚠️ Ты уже выбирал билет в этой лотерее!", show_alert=True)
            return

        # 4. Проверка, свободен ли билет
        ticket = session.query(Ticket).filter_by(lottery_id=lottery_id, number=ticket_num).first()
        if ticket.is_picked:
            await callback.answer("⚠️ Этот билет уже занят другим участником!", show_alert=True)
            return

        # 5. Оплата (Внутренний баланс Stars)
        if lottery.price_stars > 0:
            if user.balance_stars < lottery.price_stars:
                await callback.answer(f"⚠️ Недостаточно Stars на балансе! Нужно {lottery.price_stars}, у тебя {user.balance_stars}", show_alert=True)
                return
            
            # Списание у участника
            user.balance_stars -= lottery.price_stars
            user.total_spent += lottery.price_stars
            
            # Начисление организатору
            org = session.query(User).filter_by(id=lottery.organizer_id).first()
            if org:
                org.balance_stars += lottery.price_stars

        # 6. Фиксация билета
        ticket.user_id = callback.from_user.id
        ticket.is_picked = True
        ticket.picked_at = datetime.utcnow()
        
        winners = json.loads(lottery.winning_numbers)
        is_winner = ticket_num in winners
        
        if is_winner:
            ticket.is_winner = True
            user.total_won += 1
            
        session.commit()

    # Обновляем кнопки вне сессии, чтобы не держать её открытой во время сетевого запроса
    await update_lottery_buttons(bot, lottery)
    
    if is_winner:
        await callback.answer("🎉 ПОБЕДА! Ты выбрал выигрышный билет!", show_alert=True)
        await callback.message.reply(
            f"🏆 <b>Победитель!</b>\nПользователь {callback.from_user.mention_html()} выбрал билет #{ticket_num}\nЛотерея: {lottery.title}"
        )
    else:
        await callback.answer("😔 Промах... Попробуй в другой раз!", show_alert=True)


async def update_lottery_buttons(bot: Bot, lottery: Lottery):
    if not lottery.chat_id or not lottery.message_id:
        return
        
    with get_session() as session:
        tickets = session.query(Ticket).filter_by(lottery_id=lottery.id).all()
        
    buttons = []
    row = []
    for t in tickets:
        if t.is_picked:
            text = f"🏆 #{t.number}" if t.is_winner else f"❌ #{t.number}"
        else:
            text = f"#{t.number}"
        
        # Если лотерея неактивна или билет занят, убираем callback_data, чтобы нельзя было нажать
        cb_data = f"{CB_PREFIX}:ticket:{lottery.id}:{t.number}" if not t.is_picked and lottery.is_active else None
        row.append(InlineKeyboardButton(text=text, callback_data=cb_data))
        
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
        
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    try:
        await bot.edit_message_reply_markup(chat_id=lottery.chat_id, message_id=lottery.message_id, reply_markup=kb)
    except TelegramBadRequest:
        pass # Игнорируем ошибки, если сообщение было удалено или не изменено


# ================= АККАУНТ =================
@account_router.message(Command("account"))
async def cmd_account(message: Message):
    with get_session() as session:
        user = session.query(User).filter_by(id=message.from_user.id).first()
        if not user:
            user = User(id=message.from_user.id, username=message.from_user.username, first_name=message.from_user.first_name)
            session.add(user)
            session.commit()

        total_tickets = session.query(Ticket).filter_by(user_id=user.id).count()
        won_tickets = session.query(Ticket).filter_by(user_id=user.id, is_winner=True).count()

    text = (
        f"👤 <b>Твой аккаунт</b>\n\n"
        f"ID: <code>{user.id}</code>\n"
        f"💰 Баланс: <b>{user.balance_stars:.1f} Stars</b>\n"
        f"🎟 Всего билетов: <b>{total_tickets}</b>\n"
        f"🏆 Побед: <b>{won_tickets}</b>\n"
        f"📉 Потрачено: <b>{user.total_spent:.1f} Stars</b>\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Вывести Stars", callback_data=f"{CB_PREFIX}:withdraw")],
        [InlineKeyboardButton(text="📜 История", callback_data=f"{CB_PREFIX}:history")]
    ])
    await message.answer(text, reply_markup=kb)


@account_router.callback_query(F.data == f"{CB_PREFIX}:withdraw")
async def withdraw_stars(callback: CallbackQuery):
    with get_session() as session:
        user = session.query(User).filter_by(id=callback.from_user.id).first()
        if not user or user.balance_stars < 10:
            await callback.answer("⚠️ Минимум 10 Stars для вывода", show_alert=True)
            return

        # Создаем заявку на вывод и списываем баланс
        user.balance_stars -= 10 # Или user.balance_stars, если выводим всё
        withdrawal = Withdrawal(user_id=user.id, amount=10, status="pending")
        session.add(withdrawal)
        # session.commit() происходит автоматически в contextmanager

    await callback.message.answer(
        f"✅ <b>Заявка на вывод создана!</b>\n\n"
        f"Списано: <b>10 Stars</b>\n"
        f"Статус: <b>В обработке</b>\n\n"
        f"Администратор рассмотрит заявку. Твой баланс обновлен."
    )


@account_router.callback_query(F.data == f"{CB_PREFIX}:history")
async def show_history(callback: CallbackQuery):
    with get_session() as session:
        tickets = session.query(Ticket).filter_by(user_id=callback.from_user.id).order_by(Ticket.picked_at.desc()).limit(10).all()
        if not tickets:
            await callback.answer("📜 История пуста", show_alert=True)
            return

        text = "<b>Последние 10 билетов:</b>\n\n"
        for t in tickets:
            status = "🏆 ПОБЕДА" if t.is_winner else "❌ Промах"
            text += f"🎟 #{t.number} — {status} | Лотерея #{t.lottery_id}\n"
            
    await callback.message.edit_text(text)


# ================= ЗАПУСК =================
async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_routers(admin_router, lottery_router, account_router)
    
    logger.info("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
