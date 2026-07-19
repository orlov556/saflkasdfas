import sys
import os
import html
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
import logging
import json
from datetime import datetime
from contextlib import contextmanager

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, BotCommandScopeDefault

from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Boolean, DateTime, ForeignKey, Float
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

BOT_TOKEN = os.getenv("BOT_TOKEN")
CB_PREFIX = "lot"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

Base = declarative_base()
engine = create_engine("sqlite:///lottery.db")
SessionLocal = sessionmaker(bind=engine)

@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
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
    referred_by = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    tickets = relationship("Ticket", back_populates="user")
    channels = relationship("Channel", back_populates="owner")

class Channel(Base):
    __tablename__ = "channels"
    id = Column(Integer, primary_key=True)
    owner_id = Column(BigInteger, ForeignKey("users.id"))
    chat_id = Column(String)
    username = Column(String)
    title = Column(String)
    owner = relationship("User", back_populates="channels")

class Lottery(Base):
    __tablename__ = "lotteries"
    id = Column(Integer, primary_key=True)
    message_id = Column(BigInteger, nullable=True)
    chat_id = Column(String, nullable=True)
    organizer_id = Column(BigInteger, ForeignKey("users.id"))
    title = Column(String)
    media_type = Column(String, nullable=True)
    media_file_id = Column(String, nullable=True)
    total_tickets = Column(Integer)
    winners_count = Column(Integer, default=1)
    price_stars = Column(Float, default=0.0)
    winning_numbers = Column(String)
    channel_username = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
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

class CreateLottery(StatesGroup):
    text = State()
    tickets_count = State()
    winners_count = State()
    winners_input = State()
    channel = State()
    add_channel = State()
    price_mode = State()
    price_input = State()
    preview = State()
    winning_input = State()

main_router = Router()
lottery_router = Router()

def get_or_create_user(session, user_id, username=None, first_name=None):
    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        user = User(id=user_id, username=username, first_name=first_name)
        session.add(user)
        session.flush()
    return user

async def send_lottery(bot, lottery_id, edit=False, msg_id=None, chat=None):
    with get_session() as session:
        lottery = session.query(Lottery).filter_by(id=lottery_id).first()
        if not lottery:
            return
        tickets = session.query(Ticket).filter_by(lottery_id=lottery.id).all()
        text = (
            f"<b>{html.escape(lottery.title)}</b>\n\n"
            f"Билетов: <b>{lottery.total_tickets}</b>\n"
            f"Победителей: <b>{lottery.winners_count}</b>\n"
            f"Цена: <b>{'Бесплатно' if lottery.price_stars == 0 else f'{int(lottery.price_stars)} Stars'}</b>\n"
            f"Канал: <b>{html.escape(lottery.channel_username)}</b>\n\n"
            f"Выбери билет:"
        )
        buttons = []
        row = []
        for t in tickets:
            if t.is_picked:
                if t.is_winner:
                    btn = InlineKeyboardButton(text=f"Билет {t.number}", callback_data=None, button_color="#00FF00")
                else:
                    btn = InlineKeyboardButton(text=f"Билет {t.number}", callback_data=None, button_color="#FF0000")
            else:
                btn = InlineKeyboardButton(text=f"Билет {t.number}", callback_data=f"{CB_PREFIX}:ticket:{lottery.id}:{t.number}" if lottery.is_active else None, button_color="#4A90E2")
            row.append(btn)
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        cid = chat or lottery.chat_id
        mid = msg_id or lottery.message_id
        if not cid:
            return
        try:
            if edit and mid:
                if lottery.media_type == "photo":
                    await bot.edit_message_caption(cid, mid, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
                else:
                    await bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=ParseMode.HTML)
                return type('obj', (object,), {'message_id': mid, 'chat': type('obj', (object,), {'id': cid})})
            else:
                if lottery.media_type == "photo":
                    return await bot.send_photo(cid, lottery.media_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif lottery.media_type == "video":
                    return await bot.send_video(cid, lottery.media_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif lottery.media_type == "animation":
                    return await bot.send_animation(cid, lottery.media_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
                else:
                    return await bot.send_message(cid, text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except:
            return None

@main_router.message(CommandStart())
async def cmd_start(msg: Message):
    with get_session() as session:
        user = get_or_create_user(session, msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
        if len(msg.text.split()) > 1 and msg.text.split()[1].startswith("ref_"):
            try:
                ref_id = int(msg.text.split()[1].split("_")[1])
                if ref_id != msg.from_user.id and user.referred_by is None:
                    user.referred_by = ref_id
            except:
                pass
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Конкурс", callback_data=f"{CB_PREFIX}:mode:contest", button_color="#4CAF50"),
         InlineKeyboardButton(text="Фаст клик", callback_data=f"{CB_PREFIX}:mode:fast", button_color="#2196F3")],
        [InlineKeyboardButton(text="Мои конкурсы", callback_data=f"{CB_PREFIX}:my_lots", button_color="#FF9800")],
        [InlineKeyboardButton(text="Каналы", callback_data=f"{CB_PREFIX}:channels", button_color="#9C27B0")],
        [InlineKeyboardButton(text="Баланс", callback_data=f"{CB_PREFIX}:balance", button_color="#00BCD4")]
    ])
    await msg.answer(
        "<b> Добро пожаловать!</b>\n\n"
        "Хочешь провести конкурс в канале или чате?\n"
        "Я с лёгкостью тебе с этим поможу 👇\n\n"
        "<code>/new_lot</code> – создание конкурса\n"
        "<code>/my_lots</code> – управление конкурсами\n"
        "<code>/my_channels</code> – управление каналами\n"
        "<code>/support</code> – поддержка бота\n\n"
        "Нашли ошибку в боте или есть вопрос? – <code>/support</code>!",
        reply_markup=kb
    )

@main_router.callback_query(F.data.startswith(f"{CB_PREFIX}:mode:"))
async def mode_select(cb: CallbackQuery):
    await cb.answer()

@main_router.message(Command("new_lot", "newlottery"))
async def new_lot(msg: Message, state: FSMContext):
    if msg.chat.type != "private":
        return
    await state.set_state(CreateLottery.text)
    await msg.answer(
        "<b>✏️ Отправьте текст лотереи:</b>\n\n"
        "📖 Можно прикрепить фото, видео, GIF и даже музыку.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"{CB_PREFIX}:cancel", button_color="#F44336")]
        ])
    )

@main_router.message(CreateLottery.text)
async def process_text(msg: Message, state: FSMContext):
    mt, fid, cap = None, None, msg.caption or msg.text or ""
    if msg.photo:
        mt, fid = "photo", msg.photo[-1].file_id
    elif msg.video:
        mt, fid = "video", msg.video.file_id
    elif msg.animation:
        mt, fid = "animation", msg.animation.file_id
    elif msg.audio:
        mt, fid = "audio", msg.audio.file_id
    elif msg.document:
        mt, fid = "document", msg.document.file_id
    if not cap and not mt:
        await msg.answer("⚠️ Отправьте текст или медиа с описанием")
        return
    await state.update_data(title=cap, media_type=mt, media_file_id=fid)
    await state.set_state(CreateLottery.tickets_count)
    btns = []
    row = []
    for i in range(5, 101, 5):
        row.append(InlineKeyboardButton(text=str(i), callback_data=f"{CB_PREFIX}:tickets:{i}", button_color="#4A90E2"))
        if len(row) == 5:
            btns.append(row)
            row = []
    if row:
        btns.append(row)
    btns.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:start", button_color="#757575")])
    await msg.answer(
        "<b> Хорошо, теперь выберите количество билетов в лотерее:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns)
    )

@main_router.callback_query(F.data.startswith(f"{CB_PREFIX}:tickets:"))
async def tickets_count(cb: CallbackQuery, state: FSMContext):
    cnt = int(cb.data.split(":")[-1])
    await state.update_data(tickets_count=cnt)
    await state.set_state(CreateLottery.winners_count)
    await cb.message.edit_text(
        f"<b>⭐️ Отлично, теперь укажите количество победителей в лотерее (от 1 до {min(cnt, 100)}):</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="По умолчанию: 1", callback_data=f"{CB_PREFIX}:winners:1", button_color="#4CAF50")],
            [InlineKeyboardButton(text="️ Ввести своё число", callback_data=f"{CB_PREFIX}:winners:input", button_color="#FF9800")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:tickets", button_color="#757575")]
        ])
    )

@main_router.callback_query(F.data == f"{CB_PREFIX}:winners:input")
async def winners_input_mode(cb: CallbackQuery, state: FSMContext):
    await state.set_state(CreateLottery.winners_input)
    await cb.message.edit_text(
        "<b>🔢 Введите количество победителей (число):</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:tickets", button_color="#757575")]
        ])
    )

@main_router.message(CreateLottery.winners_input)
async def process_winners_input(msg: Message, state: FSMContext):
    try:
        cnt = int(msg.text)
        data = await state.get_data()
        max_w = min(data['tickets_count'], 100)
        if cnt < 1 or cnt > max_w:
            await msg.answer(f"⚠️ Число должно быть от 1 до {max_w}")
            return
        await state.update_data(winners_count=cnt)
        await state.set_state(CreateLottery.channel)
        await select_channel(msg, state)
    except:
        await msg.answer("️ Введите корректное число")

@main_router.callback_query(F.data.startswith(f"{CB_PREFIX}:winners:"))
async def winners_default(cb: CallbackQuery, state: FSMContext):
    cnt = int(cb.data.split(":")[-1])
    await state.update_data(winners_count=cnt)
    await state.set_state(CreateLottery.channel)
    await select_channel(cb.message, state)

async def select_channel(msg_or_cb, state):
    with get_session() as session:
        user_id = msg_or_cb.from_user.id if hasattr(msg_or_cb, 'from_user') else msg_or_cb.message.from_user.id
        channels = session.query(Channel).filter_by(owner_id=user_id).all()
        btns = [[InlineKeyboardButton(text=f"📢 {ch.title}", callback_data=f"{CB_PREFIX}:channel:{ch.id}", button_color="#2196F3")] for ch in channels]
        btns.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data=f"{CB_PREFIX}:addchannel", button_color="#4CAF50")])
        btns.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:tickets", button_color="#757575")])
    text = "<b>🔔 Пора выбрать канал, в котором будет опубликована лотерея:</b>"
    if isinstance(msg_or_cb, CallbackQuery):
        await msg_or_cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    else:
        await msg_or_cb.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@main_router.callback_query(F.data == f"{CB_PREFIX}:addchannel")
async def add_channel(cb: CallbackQuery, state: FSMContext):
    await state.set_state(CreateLottery.add_channel)
    await cb.message.edit_text(
        "<b>🔔 Начнём подключение канала!</b>\n\n"
        "1. Если бот уже есть на канале, отправьте @username или ссылку\n"
        "2. Если бота нет в админах – добавьте его и он подключится автоматически\n\n"
        "💡 Для приватного чата отправьте ссылку на любое сообщение\n\n"
        "<b>📖 Отправьте канал:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CB_PREFIX}:back:channel", button_color="#F44336")]
        ])
    )

@main_router.message(CreateLottery.add_channel)
async def process_add_channel(msg: Message, state: FSMContext):
    txt = msg.text.strip() if msg.text else ""
    fwd = msg.forward_from_chat if msg.forward_from_chat else None
    cid, uname, title = None, None, None
    if fwd:
        cid, uname, title = str(fwd.id), fwd.username or "", fwd.title or "Канал"
    elif txt.startswith("@") or "t.me/" in txt:
        uname = txt.replace("@", "").replace("https://t.me/", "").split("/")[0].split("?")[0]
        try:
            chat = await msg.bot.get_chat(f"@{uname}")
            cid, title = str(chat.id), chat.title or "Канал"
        except:
            await msg.answer("⚠️ Канал не найден")
            return
    else:
        await msg.answer("⚠️ Отправьте @username или ссылку")
        return
    try:
        member = await msg.bot.get_chat_member(cid, msg.bot.id)
        if member.status not in ["administrator", "creator"]:
            await msg.answer("⚠️ Бот не администратор канала")
            return
    except:
        await msg.answer("⚠️ Бот не найден в канале")
        return
    with get_session() as session:
        if not session.query(Channel).filter_by(owner_id=msg.from_user.id, chat_id=cid).first():
            session.add(Channel(owner_id=msg.from_user.id, chat_id=cid, username=uname, title=title))
    await msg.answer(f"✅ Канал <b>{title}</b> добавлен!")
    await state.set_state(CreateLottery.channel)
    await select_channel(msg, state)

@main_router.callback_query(F.data.startswith(f"{CB_PREFIX}:channel:"))
async def channel_select(cb: CallbackQuery, state: FSMContext):
    ch_id = int(cb.data.split(":")[-1])
    with get_session() as session:
        ch = session.query(Channel).filter_by(id=ch_id, owner_id=cb.from_user.id).first()
        if ch:
            await state.update_data(channel_chat_id=ch.chat_id, channel_username=f"@{ch.username}" if ch.username else ch.title)
    await state.set_state(CreateLottery.price_mode)
    await cb.message.edit_text(
        "<b>💸 Хорошо, теперь определим цену участия в лотерее:</b>\n\n"
        " На выбор доступно два варианта: бесплатно и платно (Stars)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" Бесплатно", callback_data=f"{CB_PREFIX}:price:free", button_color="#4CAF50"),
             InlineKeyboardButton(text="💰 Платно", callback_data=f"{CB_PREFIX}:price:paid", button_color="#FF9800")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:channel", button_color="#757575")]
        ])
    )

@main_router.callback_query(F.data == f"{CB_PREFIX}:price:paid")
async def price_input_mode(cb: CallbackQuery, state: FSMContext):
    await state.set_state(CreateLottery.price_input)
    await cb.message.edit_text(
        "<b>💰 Введите стоимость билета в Stars (от 1 до 2500):</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:price_mode", button_color="#757575")]
        ])
    )

@main_router.message(CreateLottery.price_input)
async def process_price_input(msg: Message, state: FSMContext):
    try:
        price = int(msg.text)
        if price < 1 or price > 2500:
            await msg.answer("⚠️ Цена должна быть от 1 до 2500 Stars")
            return
        await state.update_data(price=price)
        await state.set_state(CreateLottery.winning_input)
        data = await state.get_data()
        await msg.answer(
            f"<b>🎯 Теперь выберите выигрышные билеты!</b>\n\n"
            f"Победителей: <b>{data['winners_count']}</b>\n"
            f"Введите номера через запятую (например: 3,7,12)\n\n"
            f"Диапазон: 1-{data['tickets_count']}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="️ Назад", callback_data=f"{CB_PREFIX}:back:price_mode", button_color="#757575")]
            ])
        )
    except:
        await msg.answer("⚠️ Введите корректное число от 1 до 2500")

@main_router.callback_query(F.data == f"{CB_PREFIX}:price:free")
async def price_free(cb: CallbackQuery, state: FSMContext):
    await state.update_data(price=0)
    await state.set_state(CreateLottery.winning_input)
    data = await state.get_data()
    await cb.message.edit_text(
        f"<b>🎯 Теперь выберите выигрышные билеты!</b>\n\n"
        f"Победителей: <b>{data['winners_count']}</b>\n"
        f"Введите номера через запятую (например: 3,7,12)\n\n"
        f"Диапазон: 1-{data['tickets_count']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="️ Назад", callback_data=f"{CB_PREFIX}:back:price_mode", button_color="#757575")]
        ])
    )

@main_router.message(CreateLottery.winning_input)
async def process_winning_input(msg: Message, state: FSMContext):
    try:
        data = await state.get_data()
        nums = [int(x.strip()) for x in msg.text.split(",") if x.strip().isdigit()]
        if len(nums) != data['winners_count']:
            await msg.answer(f"⚠️ Нужно указать ровно {data['winners_count']} номеров (победителей)")
            return
        if any(n < 1 or n > data['tickets_count'] for n in nums):
            await msg.answer(f"⚠️ Номера должны быть от 1 до {data['tickets_count']}")
            return
        if len(set(nums)) != len(nums):
            await msg.answer("️ Номера не должны повторяться")
            return
        await state.update_data(winning_numbers=nums)
        await state.set_state(CreateLottery.preview)
        price_txt = "Бесплатно" if data['price'] == 0 else f"{int(data['price'])} Stars"
        win_txt = ", ".join(f"#{n}" for n in nums)
        text = (
            f"<b>👁‍ Предпросмотр лотереи:</b>\n\n"
            f"🎟 Количество билетов » <b>{data['tickets_count']}</b>\n"
            f"  ⤷ Победителей » <b>{data['winners_count']}</b>\n"
            f"  ⤷ Текст кнопок » <b>🎟 {data['tickets_count']}</b>\n\n"
            f" Цена участия » <b>{price_txt}</b>\n"
            f"🔔 Канал » <b>{data.get('channel_username', '—')}</b>\n\n"
            f"📖 Если всё верно, то подтвердите создание лотереи:"
        )
        await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Завершить создание", callback_data=f"{CB_PREFIX}:finish", button_color="#4CAF50")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:winning", button_color="#757575"),
             InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_PREFIX}:cancel", button_color="#F44336")]
        ]))
    except:
        await msg.answer("⚠️ Ошибка формата. Введите числа через запятую (например: 3,7,12)")

@main_router.callback_query(F.data == f"{CB_PREFIX}:finish")
async def finish_lottery(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lid = None
    with get_session() as session:
        lottery = Lottery(
            organizer_id=cb.from_user.id,
            title=data['title'],
            media_type=data.get('media_type'),
            media_file_id=data.get('media_file_id'),
            total_tickets=data['tickets_count'],
            winners_count=data['winners_count'],
            price_stars=data['price'],
            winning_numbers=json.dumps(data['winning_numbers']),
            channel_username=data.get('channel_username'),
            chat_id=data.get('channel_chat_id')
        )
        session.add(lottery)
        session.flush()
        lid = lottery.id
        for i in range(1, data['tickets_count'] + 1):
            session.add(Ticket(lottery_id=lottery.id, number=i))
    if lid:
        try:
            sent = await send_lottery(cb.bot, lid)
            if sent:
                with get_session() as session:
                    lot = session.query(Lottery).filter_by(id=lid).first()
                    lot.message_id = sent.message_id
                    lot.chat_id = str(sent.chat.id)
                await state.clear()
                await cb.message.edit_text(f"✅ <b>Лотерея создана!</b>\nID: <code>{lid}</code>")
            else:
                await cb.message.edit_text("❌ Ошибка публикации")
        except Exception as e:
            await cb.message.edit_text(f"❌ Ошибка: {e}")

@main_router.callback_query(F.data == f"{CB_PREFIX}:cancel")
async def cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Отменено")

@main_router.callback_query(F.data.startswith(f"{CB_PREFIX}:back:"))
async def go_back(cb: CallbackQuery, state: FSMContext):
    step = cb.data.split(":")[-1]
    await state.clear()
    await cb.message.edit_text("❌ Возврат. Введите /new_lot")

@lottery_router.callback_query(F.data.startswith(f"{CB_PREFIX}:ticket:"))
async def pick_ticket(cb: CallbackQuery):
    parts = cb.data.split(":")
    lid, tnum = int(parts[2]), int(parts[3])
    with get_session() as session:
        lottery = session.query(Lottery).filter_by(id=lid).first()
        if not lottery or not lottery.is_active:
            await cb.answer("🚫 Завершена", show_alert=True)
            return
        user = get_or_create_user(session, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        existing = session.query(Ticket).filter_by(lottery_id=lid, user_id=cb.from_user.id).first()
        if existing:
            await cb.answer("⚠️ Вы уже выбрали билет", show_alert=True)
            return
        ticket = session.query(Ticket).filter_by(lottery_id=lid, number=tnum).first()
        if ticket.is_picked:
            await cb.answer("⚠️ Билет занят", show_alert=True)
            return
        if lottery.price_stars > 0:
            if user.balance_stars < lottery.price_stars:
                await cb.answer(f"⚠️ Нужно {lottery.price_stars} Stars", show_alert=True)
                return
            user.balance_stars -= lottery.price_stars
            user.total_spent += lottery.price_stars
            org = session.query(User).filter_by(id=lottery.organizer_id).first()
            if org:
                org.balance_stars += lottery.price_stars
        ticket.user_id = cb.from_user.id
        ticket.is_picked = True
        ticket.picked_at = datetime.utcnow()
        winners = json.loads(lottery.winning_numbers)
        if tnum in winners:
            ticket.is_winner = True
            user.total_won += 1
        await send_lottery(cb.bot, lid, edit=True, msg_id=lottery.message_id, chat=lottery.chat_id)
        await cb.answer(" ПОБЕДА!" if ticket.is_winner else "😔 Промах...", show_alert=True)

@main_router.message(Command("account", "balance"))
async def cmd_account(msg: Message):
    with get_session() as session:
        user = get_or_create_user(session, msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
        total = session.query(Ticket).filter_by(user_id=user.id).count()
        won = session.query(Ticket).filter_by(user_id=user.id, is_winner=True).count()
    prem = "✅ Premium" if getattr(msg.from_user, 'is_premium', False) else "Нет"
    reg = user.created_at.strftime("%d.%m.%Y %H:%M") if user.created_at else "—"
    text = (
        f"<b>👤 Ваш аккаунт:</b>\n\n"
        f" Подписка » {prem}\n\n"
        f"👤 UUID » <code>{user.id}</code>\n"
        f"   Регистрация » <code>{reg}</code>\n\n"
        f"<b> Балансы:</b>\n"
        f"  ⤷ Звёзды » <b>{user.balance_stars:.2f}</b> | Удержание » <b>0.00</b>\n"
        f"   Доллары » <b>$0.00</b>\n"
        f"   Гемы » <b>0</b>\n\n"
        f"⭐️ История трат звёзд: <b>{user.total_spent:.2f}</b>\n\n"
        f"💬 <a href='https://t.me/support'>Присоединяйтесь к админскому чату бота</a>.\n\n"
        f"<b> Управление аккаунтом:</b>"
    )
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Вывести", callback_data=f"{CB_PREFIX}:withdraw", button_color="#FF9800")],
        [InlineKeyboardButton(text="📜 История", callback_data=f"{CB_PREFIX}:history", button_color="#2196F3")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"{CB_PREFIX}:settings", button_color="#9C27B0")]
    ]), disable_web_page_preview=True)

async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_routers(main_router, lottery_router)
    logging.info("Bot started")
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
