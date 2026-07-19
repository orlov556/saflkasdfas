# handlers/admin.py
import random
import json
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from models.database import get_session, Lottery, Ticket, User
from config import MIN_TICKETS, MAX_TICKETS, MAX_WINNERS, CB_PREFIX

router = Router()


class CreateLottery(StatesGroup):
    title = State()
    tickets_count = State()
    winners_count = State()
    price = State()
    conditions = State()


@router.message(Command("newlottery"))
async def cmd_new_lottery(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    
    await state.set_state(CreateLottery.title)
    await message.answer(
        "<b>Создание лотереи</b>\n\nВведи название розыгрыша:",
        parse_mode="HTML"
    )


@router.message(CreateLottery.title)
async def process_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(CreateLottery.tickets_count)
    await message.answer(
        f"Название: <b>{message.text}</b>\n\nСколько билетов? (от {MIN_TICKETS} до {MAX_TICKETS})",
        parse_mode="HTML"
    )


@router.message(CreateLottery.tickets_count)
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
    await message.answer(
        f"Билетов: <b>{count}</b>\n\nСколько победителей? (от 1 до {min(count, MAX_WINNERS)})",
        parse_mode="HTML"
    )


@router.message(CreateLottery.winners_count)
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


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:price:"))
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
    
    await callback.message.edit_text(
        "Дополнительные условия участия:",
        reply_markup=kb
    )


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:cond:"))
async def process_conditions(callback: CallbackQuery, state: FSMContext, bot: Bot):
    cond = callback.data.split(":")[-1]
    data = await state.get_data()
    
    conditions = {
        'premium': False,
        'boost': False,
        'subscription': False,
        'sub_chat_id': None
    }
    
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
        user = User(
            id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name
        )
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
        row.append(InlineKeyboardButton(
            text=f"#{i}",
            callback_data=f"{CB_PREFIX}:ticket:{lottery_id}:{i}"
        ))
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
    
    await callback.message.edit_text(
        "Лотерея создана! Отправь этот пост в канал:\n\n"
        f"<code>/publish_{lottery_id}</code>",
        parse_mode="HTML"
    )
    
    await state.update_data(publish_text=text, publish_kb=kb)
