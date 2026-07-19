# bot.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
import logging
import random
import json
from datetime import datetime
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Boolean, DateTime, ForeignKey, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

BOT_TOKEN = os.getenv("BOT_TOKEN", "8838134787:AAEyBkhhFthT4Tfp5o_YM47BiVyzfqQ8Y4g")
MIN_TICKETS = 5
MAX_TICKETS = 100
MAX_WINNERS = 100
CB_PREFIX = "lot"

Base = declarative_base()
engine = create_engine("sqlite:///lottery.db", echo=False)
Session = sessionmaker(bind=engine)


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
    message_id = Column(BigInteger)
    chat_id = Column(BigInteger)
    organizer_id = Column(BigInteger, ForeignKey("users.id"))
    title = Column(String)
    total_tickets = Column(Integer)
    winners_count = Column(Integer, default=1)
    price_stars = Column(Float, default=0.0)
    winning_numbers = Column(String)
    require_premium = Column(Boolean, default=False)
    require_boost = Column(Boolean, default=False)
    require_subscription = Column(Boolean, default=False)
    subscription_chat_id = Column(BigInteger, nullable=True)
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


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    return Session()


class CreateLottery(StatesGroup):
    title = State()
    tickets_count = State()
    winners_count = State()
    price = State()
    conditions = State()


admin_router = Router()
lottery_router = Router()
account_router = Router()


@admin_router.message(Command("newlottery"))
async def cmd_new_lottery(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    await state.set_state(CreateLottery.title)
    await message.answer("<b>Создание лотереи</b>\n\nВведи название розыгрыша:", parse_mode="HTML")


@admin_router.message(CreateLottery.title)
async def process_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(CreateLottery.tickets_count)
    await message.answer(f"Название: <b>{message.text}</b>\n\nСколько билетов? (от {MIN_TICKETS} до {MAX_TICKETS})", parse_mode="HTML")


@admin_router.message(CreateLottery.tickets_count)
async def process_tickets(message: Message, state: FSMContext):
    try:
        count = int(message.text)
        if not (MIN_TICKETS <= count <= MAX_TICKETS):
            raise ValueError
    except ValueError:
        await message.answer(f"Введи число от {MIN_TICKETS} до {MAX_TICKETS}")
        return
    await state.update_data(tickets_count=count)
    await state.set_state(CreateLottery.winners_count)
    await message.answer(f"Билетов: <b>{count}</b>\n\nСколько победителей? (от 1 до {min(count, MAX_WINNERS)})", parse_mode="HTML")


@admin_router.message(CreateLottery.winners_count)
async def process_winners(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        count = int(message.text)
        if not (1 <= count <= min(data['tickets_count'], MAX_WINNERS)):
            raise ValueError
    except ValueError:
        await message.answer("Некорректное количество победителей")
        return
    await state.update_data(winners_count=count)
    await state.set_state(CreateLottery.price)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Бесплатно", callback_data=f"{CB_PREFIX}:price:0")],
        [InlineKeyboardButton(text="1 Star", callback_data=f"{CB_PREFIX}:price:1")],
        [InlineKeyboardButton(text="5 Stars", callback_data=f"{CB_PREFIX}:price:5")],
        [InlineKeyboardButton(text="10 Stars", callback_data=f"{CB_PREFIX}:price:10")]
    ])
    await message.answer("Выбери стоимость билета:", reply_markup=kb)


@admin_router.callback_query(F.data.startswith(f"{CB_PREFIX}:price:"))
async def process_price(callback: CallbackQuery, state: FSMContext):
    price = float(callback.data.split(":")[-1])
    await state.update_data(price=price)
    await state.set_state(CreateLottery.conditions)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Premium", callback_data=f"{CB_PREFIX}:cond:premium")],
        [InlineKeyboardButton(text="Boost", callback_data=f"{CB_PREFIX}:cond:boost")],
        [InlineKeyboardButton(text="Подписка на канал", callback_data=f"{CB_PREFIX}:cond:sub")],
        [InlineKeyboardButton(text="Пропустить", callback_data=f"{CB_PREFIX}:cond:skip")]
    ])
    await callback.message.edit_text("Дополнительные условия участия:", reply_markup=kb)


@admin_router.callback_query(F.data.startswith(f"{CB_PREFIX}:cond:"))
async def process_conditions(callback: CallbackQuery, state: FSMContext, bot: Bot):
    cond = callback.data.split(":")[-1]
    data = await state.get_data()
    conditions = {'premium': False, 'boost': False, 'subscription': False, 'sub_chat_id': None}
    if cond == "premium":
        conditions['premium'] = True
    elif cond == "boost":
        conditions['boost'] = True
    elif cond == "sub":
        conditions['subscription'] = True
    total = data['tickets_count']
    winners = random.sample(range(1, total + 1), data['winners_count'])
    session = get_session()
    user = session.query(User).filter_by(id=callback.from_user.id).first()
    if not user:
        user = User(id=callback.from_user.id, username=callback.from_user.username, first_name=callback.from_user.first_name)
        session.add(user)
        session.commit()
    lottery = Lottery(
        organizer_id=callback.from_user.id,
        title=data['title'],
        total_tickets=total,
        winners_count=data['winners_count'],
        price_stars=data['price'],
        winning_numbers=json.dumps(winners),
        require_premium=conditions['premium'],
        require_boost=conditions['boost'],
        require_subscription=conditions['subscription']
    )
    session.add(lottery)
    session.commit()
    for i in range(1, total + 1):
        ticket = Ticket(lottery_id=lottery.id, number=i)
        session.add(ticket)
    session.commit()
    lottery_id = lottery.id
    session.close()
    await state.clear()
    buttons = []
    row = []
    for i in range(1, total + 1):
        row.append(InlineKeyboardButton(text=f"#{i}", callback_data=f"{CB_PREFIX}:ticket:{lottery_id}:{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton(
            text="Добавить бота в админы",
            url=f"https://t.me/{(await bot.me()).username}?startchannel&admin=post_messages+edit_messages+delete_messages"
        )
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = (
        f"<b>{data['title']}</b>\n\n"
        f"Билетов: <b>{total}</b>\n"
        f"Победителей: <b>{data['winners_count']}</b>\n"
        f"Стоимость: <b>{'Бесплатно' if data['price'] == 0 else f'{int(data['price'])} Stars'}</b>\n"
    )
    if conditions['premium']:
        text += "Требуется <b>Premium</b>\n"
    if conditions['boost']:
        text += "Требуется <b>Boost</b>\n"
    if conditions['subscription']:
        text += "Требуется <b>подписка</b>\n"
    text += "\nВыбери билет и испытай удачу!"
    await callback.message.edit_text("Лотерея создана! Отправь этот пост в канал:\n\n" + f"<code>/publish_{lottery_id}</code>", parse_mode="HTML")
    await state.update_data(publish_text=text, publish_kb=kb)


@lottery_router.callback_query(F.data.startswith(f"{CB_PREFIX}:ticket:"))
async def pick_ticket(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    lottery_id = int(parts[2])
    ticket_num = int(parts[3])
    session = get_session()
    lottery = session.query(Lottery).filter_by(id=lottery_id).first()
    if not lottery or not lottery.is_active:
        await callback.answer("Лотерея завершена", show_alert=True)
        session.close()
        return
    user = session.query(User).filter_by(id=callback.from_user.id).first()
    if lottery.require_premium:
        chat_member = await bot.get_chat_member(lottery.chat_id, callback.from_user.id)
        if not getattr(chat_member, 'is_premium', False):
            await callback.answer("Нужен Telegram Premium!", show_alert=True)
            session.close()
            return
    if lottery.require_subscription and lottery.subscription_chat_id:
        try:
            member = await bot.get_chat_member(lottery.subscription_chat_id, callback.from_user.id)
            if member.status in ["left", "kicked"]:
                await callback.answer("Подпишись на канал!", show_alert=True)
                session.close()
                return
        except:
            pass
    existing = session.query(Ticket).filter_by(lottery_id=lottery_id, user_id=callback.from_user.id).first()
    if existing:
        await callback.answer("Ты уже выбирал билет!", show_alert=True)
        session.close()
        return
    ticket = session.query(Ticket).filter_by(lottery_id=lottery_id, number=ticket_num).first()
    if ticket.is_picked:
        await callback.answer("Этот билет уже занят!", show_alert=True)
        session.close()
        return
    if lottery.price_stars > 0:
        pass
    if not user:
        user = User(id=callback.from_user.id, username=callback.from_user.username, first_name=callback.from_user.first_name)
        session.add(user)
    ticket.user_id = callback.from_user.id
    ticket.is_picked = True
    ticket.picked_at = datetime.utcnow()
    winners = json.loads(lottery.winning_numbers)
    is_winner = ticket_num in winners
    if is_winner:
        ticket.is_winner = True
        user.total_won += 1
        picked_winners = session.query(Ticket).filter_by(lottery_id=lottery_id, is_winner=True).count()
        if picked_winners >= lottery.winners_count:
            lottery.is_active = False
    session.commit()
    await update_lottery_buttons(bot, lottery, session)
    if is_winner:
        await callback.answer("ПОБЕДА! Ты выбрал выигрышный билет!", show_alert=True)
        await callback.message.reply(
            f"<b>Победитель!</b>\nПользователь {callback.from_user.mention_html()} выбрал билет #{ticket_num}\nЛотерея: {lottery.title}",
            parse_mode="HTML"
        )
    else:
        await callback.answer("Промах... Попробуй в другой раз!", show_alert=True)
    if lottery.price_stars > 0:
        org = session.query(User).filter_by(id=lottery.organizer_id).first()
        if org:
            org.balance_stars += lottery.price_stars
            session.commit()
    session.close()


async def update_lottery_buttons(bot: Bot, lottery: Lottery, session):
    tickets = session.query(Ticket).filter_by(lottery_id=lottery.id).all()
    buttons = []
    row = []
    for t in tickets:
        if t.is_picked:
            text = f"[WIN] #{t.number}" if t.is_winner else f"[X] #{t.number}"
        else:
            text = f"#{t.number}"
        row.append(InlineKeyboardButton(text=text, callback_data=f"{CB_PREFIX}:ticket:{lottery.id}:{t.number}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await bot.edit_message_reply_markup(chat_id=lottery.chat_id, message_id=lottery.message_id, reply_markup=kb)
    except TelegramBadRequest:
        pass


@account_router.message(Command("account"))
async def cmd_account(message: Message):
    session = get_session()
    user = session.query(User).filter_by(id=message.from_user.id).first()
    if not user:
        user = User(id=message.from_user.id, username=message.from_user.username, first_name=message.from_user.first_name)
        session.add(user)
        session.commit()
    total_tickets = session.query(Ticket).filter_by(user_id=user.id).count()
    won_tickets = session.query(Ticket).filter_by(user_id=user.id, is_winner=True).count()
    text = (
        f"<b>Твой аккаунт</b>\n\n"
        f"ID: <code>{user.id}</code>\n"
        f"Баланс: <b>{user.balance_stars:.1f} Stars</b>\n"
        f"Всего билетов: <b>{total_tickets}</b>\n"
        f"Побед: <b>{won_tickets}</b>\n"
        f"Потрачено: <b>{user.total_spent:.1f} Stars</b>\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Вывести Stars", callback_data=f"{CB_PREFIX}:withdraw")],
        [InlineKeyboardButton(text="История", callback_data=f"{CB_PREFIX}:history")]
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)
    session.close()


@account_router.callback_query(F.data == f"{CB_PREFIX}:withdraw")
async def withdraw_stars(callback: CallbackQuery):
    session = get_session()
    user = session.query(User).filter_by(id=callback.from_user.id).first()
    if not user or user.balance_stars < 1:
        await callback.answer("Недостаточно Stars для вывода", show_alert=True)
        session.close()
        return
    await callback.message.answer(
        f"<b>Запрос на вывод</b>\n\n"
        f"Доступно: <b>{user.balance_stars:.1f} Stars</b>\n"
        f"Минимум: <b>10 Stars</b>\n\n"
        f"Напиши @admin для вывода средств.",
        parse_mode="HTML"
    )
    session.close()


@account_router.callback_query(F.data == f"{CB_PREFIX}:history")
async def show_history(callback: CallbackQuery):
    session = get_session()
    tickets = session.query(Ticket).filter_by(user_id=callback.from_user.id).order_by(Ticket.picked_at.desc()).limit(10).all()
    if not tickets:
        await callback.answer("История пуста", show_alert=True)
        session.close()
        return
    text = "<b>Последние 10 билетов:</b>\n\n"
    for t in tickets:
        status = "ПОБЕДА" if t.is_winner else "Промах"
        text += f"#{t.number} — {status} | Лотерея #{t.lottery_id}\n"
    await callback.message.edit_text(text, parse_mode="HTML")
    session.close()


async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    dp = Dispatcher()
    dp.include_routers(admin_router, lottery_router, account_router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
