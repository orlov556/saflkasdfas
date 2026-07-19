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
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    PhotoSize, Animation, Video, Audio, Document, BufferedInputFile
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Boolean, DateTime, ForeignKey, Float, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ")
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
    language = Column(String, default="ru")
    notify_referrals = Column(Boolean, default=True)
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
    media_type = Column(String, nullable=True)  # photo, video, animation, audio, document
    media_file_id = Column(String, nullable=True)
    total_tickets = Column(Integer)
    winners_count = Column(Integer, default=1)
    price_stars = Column(Float, default=0.0)
    winning_numbers = Column(String)  # JSON list
    channel_username = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    organizer = relationship("User")
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
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)


# ================= FSM =================
class CreateLottery(StatesGroup):
    text = State()           # Этап 1: текст + медиа
    tickets_count = State()  # Этап 2: количество билетов
    winners_count = State()  # Этап 3: количество победителей
    channel = State()        # Этап 4: выбор канала
    add_channel = State()    # Добавление нового канала
    price = State()          # Этап 5: цена
    preview = State()        # Этап 6: предпросмотр
    winning_tickets = State() # Выбор выигрышных билетов


class SettingsStates(StatesGroup):
    waiting_language = State()


# ================= РОУТЕРЫ =================
admin_router = Router()
lottery_router = Router()
account_router = Router()
settings_router = Router()


# ================= УТИЛИТЫ =================
def get_or_create_user(session, user_id, username=None, first_name=None):
    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        user = User(id=user_id, username=username, first_name=first_name)
        session.add(user)
        session.flush()
    return user


def build_tickets_keyboard(lottery_id, total_tickets):
    """Строим клавиатуру с билетами в виде 🎟️"""
    buttons = []
    row = []
    for i in range(1, total_tickets + 1):
        row.append(InlineKeyboardButton(text=f"🎟️ {i}", callback_data=f"{CB_PREFIX}:ticket:{lottery_id}:{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_lottery_message(bot: Bot, lottery: Lottery):
    """Отправка/обновление сообщения лотереи с кнопками"""
    text = (
        f"🎉 <b>{lottery.title}</b>\n\n"
        f"🎟 Билетов: <b>{lottery.total_tickets}</b>\n"
        f"🏆 Победителей: <b>{lottery.winners_count}</b>\n"
        f"💰 Цена: <b>{'Бесплатно' if lottery.price_stars == 0 else f'{int(lottery.price_stars)} Stars'}</b>\n"
        f"🔔 Канал: <b>{lottery.channel_username}</b>\n\n"
        f"👇 <b>Выбери билет и испытай удачу!</b>"
    )
    
    with get_session() as session:
        tickets = session.query(Ticket).filter_by(lottery_id=lottery.id).all()
        
        buttons = []
        row = []
        for t in tickets:
            if t.is_picked:
                if t.is_winner:
                    btn_text = f"🟢 {t.number}"  # "зелёная" кнопка
                else:
                    btn_text = f"🔴 {t.number}"  # "красная" кнопка
                cb_data = None
            else:
                btn_text = f"🎟️ {t.number}"
                cb_data = f"{CB_PREFIX}:ticket:{lottery.id}:{t.number}" if lottery.is_active else None
            row.append(InlineKeyboardButton(text=btn_text, callback_data=cb_data))
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    # Если есть медиа — отправляем с медиа
    if lottery.media_type and lottery.media_file_id:
        if lottery.media_type == "photo":
            return await bot.send_photo(chat_id=lottery.chat_id, photo=lottery.media_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
        elif lottery.media_type == "video":
            return await bot.send_video(chat_id=lottery.chat_id, video=lottery.media_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
        elif lottery.media_type == "animation":
            return await bot.send_animation(chat_id=lottery.chat_id, animation=lottery.media_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
        elif lottery.media_type == "audio":
            return await bot.send_audio(chat_id=lottery.chat_id, audio=lottery.media_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
        elif lottery.media_type == "document":
            return await bot.send_document(chat_id=lottery.chat_id, document=lottery.media_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
    return await bot.send_message(chat_id=lottery.chat_id, text=text, reply_markup=kb, parse_mode=ParseMode.HTML)


# ================= /start и /help =================
@admin_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    args = message.text.split()
    with get_session() as session:
        user = get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.first_name)
        
        # Реферальная система
        if len(args) > 1 and args[1].startswith("ref_"):
            try:
                ref_id = int(args[1].split("_")[1])
                if ref_id != message.from_user.id and user.referred_by is None:
                    user.referred_by = ref_id
                    # Уведомление рефереру
                    referrer = session.query(User).filter_by(id=ref_id).first()
                    if referrer and referrer.notify_referrals:
                        try:
                            await message.bot.send_message(
                                ref_id,
                                f"🎉 <b>Новый реферал!</b>\n\n"
                                f"Пользователь {message.from_user.mention_html()} присоединился по вашей ссылке!"
                            )
                        except Exception:
                            pass
            except (ValueError, IndexError):
                pass
        session.commit()

    text = (
        "🎰 <b>Добро пожаловать в Лотерею!</b>\n\n"
        "📋 <b>Команды:</b>\n"
        "• /newlottery — создать лотерею\n"
        "• /account — ваш аккаунт\n"
        "• /help — помощь\n\n"
        f"🔗 <b>Ваша реферальная ссылка:</b>\n"
        f"<code>https://t.me/{(await message.bot.get_me()).username}?start=ref_{message.from_user.id}</code>"
    )
    await message.answer(text)


@admin_router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📖 <b>Помощь</b>\n\n"
        "<b>Для организаторов:</b>\n"
        "• /newlottery — создать лотерею\n"
        "• /account — аккаунт и баланс\n\n"
        "<b>Для участников:</b>\n"
        "• Нажимайте на кнопки 🎟️ в лотереях\n"
        "• 🟢 — победа, 🔴 — промах\n\n"
        "<b>Рефералы:</b>\n"
        "• Делитесь своей ссылкой из /start\n"
        "• Получайте уведомления о новых рефералах"
    )
    await message.answer(text)


# ================= СОЗДАНИЕ ЛОТЕРЕИ =================
@admin_router.message(Command("newlottery"))
async def cmd_new_lottery(message: Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("⚠️ Создание лотереи доступно только в личных сообщениях!")
        return
    await state.set_state(CreateLottery.text)
    await message.answer(
        "✏️ <b>Отправьте текст лотереи:</b>\n\n"
        "📖 Можно прикрепить фото, видео, GIF и даже музыку."
    )


@admin_router.message(CreateLottery.text)
async def process_text(message: Message, state: FSMContext):
    # Определяем медиа
    media_type = None
    file_id = None
    caption = message.caption or message.text or ""
    
    if message.photo:
        media_type = "photo"
        file_id = message.photo[-1].file_id
    elif message.video:
        media_type = "video"
        file_id = message.video.file_id
    elif message.animation:
        media_type = "animation"
        file_id = message.animation.file_id
    elif message.audio:
        media_type = "audio"
        file_id = message.audio.file_id
    elif message.document:
        media_type = "document"
        file_id = message.document.file_id
    
    if not caption and not media_type:
        await message.answer("⚠️ Отправьте текст или медиа с подписью")
        return
    
    await state.update_data(title=caption or "Лотерея", media_type=media_type, media_file_id=file_id)
    await state.set_state(CreateLottery.tickets_count)
    
    # Кнопки с количеством билетов
    buttons = []
    row = []
    for i in range(5, 101, 5):
        row.append(InlineKeyboardButton(text=str(i), callback_data=f"{CB_PREFIX}:tickets:{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:text"),
                    InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_PREFIX}:cancel")])
    
    await message.answer(
        "🎟 <b>Хорошо, теперь выберите количество билетов в лотерее:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@admin_router.callback_query(F.data.startswith(f"{CB_PREFIX}:tickets:"))
async def process_tickets_count(callback: CallbackQuery, state: FSMContext):
    count = int(callback.data.split(":")[-1])
    await state.update_data(tickets_count=count)
    await state.set_state(CreateLottery.winners_count)
    
    # Кнопки для победителей (от 1 до min(10, count))
    max_w = min(10, count)
    buttons = []
    row = []
    for i in range(1, max_w + 1):
        row.append(InlineKeyboardButton(text=str(i), callback_data=f"{CB_PREFIX}:winners:{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:tickets"),
                    InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_PREFIX}:cancel")])
    
    await callback.message.edit_text(
        f"⭐️ <b>Отлично, теперь укажите количество победителей в лотерее (от 1 до {max_w}):</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@admin_router.callback_query(F.data.startswith(f"{CB_PREFIX}:winners:"))
async def process_winners_count(callback: CallbackQuery, state: FSMContext):
    count = int(callback.data.split(":")[-1])
    await state.update_data(winners_count=count)
    await state.set_state(CreateLottery.channel)
    
    # Получаем каналы пользователя
    with get_session() as session:
        channels = session.query(Channel).filter_by(owner_id=callback.from_user.id).all()
        
        buttons = []
        for ch in channels:
            buttons.append([InlineKeyboardButton(text=f"📢 {ch.title}", callback_data=f"{CB_PREFIX}:channel:{ch.id}")])
        buttons.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data=f"{CB_PREFIX}:addchannel")])
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:winners"),
                        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_PREFIX}:cancel")])
    
    await callback.message.edit_text(
        "🔔 <b>Пора выбрать канал, в котором будет опубликована лотерея:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@admin_router.callback_query(F.data == f"{CB_PREFIX}:addchannel")
async def add_channel_prompt(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateLottery.add_channel)
    text = (
        "🔔 <b>Начнём подключение канала!</b>\n\n"
        "1. Если бот уже есть на канале, то просто отправьте юзернейм (@channel) канала или ссылку на него. "
        "Также можно просто переслать сюда любую публикацию с канала или ссылку на неё.\n\n"
        "2. Если этот бот не добавлен в администраторы канала, то нажмите кнопку «Отмена» и добавьте бота "
        "в администраторы канала — он подключится автоматически.\n\n"
        "💡 <b>Совет.</b> Если Вы хотите добавить не канал, а приватный чат, который не имеет публичной ссылки, "
        "то отправьте боту ссылку на любое сообщение из того чата.\n\n"
        "📖 <b>Отправьте канал:</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CB_PREFIX}:back:channel")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)


@admin_router.message(CreateLottery.add_channel)
async def process_add_channel(message: Message, state: FSMContext, bot: Bot):
    text = message.text.strip() if message.text else ""
    forward = message.forward_from_chat if message.forward_from_chat else None
    
    chat_id = None
    username = None
    title = None
    
    if forward:
        chat_id = str(forward.id)
        username = forward.username or ""
        title = forward.title or "Канал"
    elif text.startswith("@") or text.startswith("https://t.me/"):
        # Извлекаем username
        if text.startswith("@"):
            username = text[1:]
        else:
            parts = text.split("/")
            for p in parts:
                if p and not p.startswith("t.me") and not p.startswith("http") and not p[0].isdigit():
                    username = p
                    break
        if not username:
            await message.answer("⚠️ Не удалось распознать канал. Попробуйте ещё раз.")
            return
        try:
            chat = await bot.get_chat(f"@{username}")
            chat_id = str(chat.id)
            title = chat.title or "Канал"
        except TelegramBadRequest:
            await message.answer("⚠️ Канал не найден. Проверьте правильность username.")
            return
    else:
        await message.answer("⚠️ Отправьте @username или ссылку на канал.")
        return
    
    # Проверяем, что бот там админ
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
        if member.status not in ["administrator", "creator"]:
            await message.answer("⚠️ Бот не является администратором этого канала. Добавьте его в админы!")
            return
    except TelegramBadRequest:
        await message.answer("⚠️ Бот не найден в этом канале.")
        return
    
    # Сохраняем канал
    with get_session() as session:
        existing = session.query(Channel).filter_by(owner_id=message.from_user.id, chat_id=chat_id).first()
        if not existing:
            new_ch = Channel(owner_id=message.from_user.id, chat_id=chat_id, username=username, title=title)
            session.add(new_ch)
            session.commit()
    
    await message.answer(f"✅ Канал <b>{title}</b> успешно добавлен!")
    await state.set_state(CreateLottery.channel)
    
    # Возвращаемся к выбору канала
    with get_session() as session:
        channels = session.query(Channel).filter_by(owner_id=message.from_user.id).all()
        buttons = []
        for ch in channels:
            buttons.append([InlineKeyboardButton(text=f"📢 {ch.title}", callback_data=f"{CB_PREFIX}:channel:{ch.id}")])
        buttons.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data=f"{CB_PREFIX}:addchannel")])
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:winners"),
                        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_PREFIX}:cancel")])
    
    await message.answer(
        "🔔 <b>Выберите канал для публикации:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@admin_router.callback_query(F.data.startswith(f"{CB_PREFIX}:channel:"))
async def process_channel_choice(callback: CallbackQuery, state: FSMContext):
    ch_id = int(callback.data.split(":")[-1])
    with get_session() as session:
        channel = session.query(Channel).filter_by(id=ch_id, owner_id=callback.from_user.id).first()
        if not channel:
            await callback.answer("⚠️ Канал не найден", show_alert=True)
            return
        await state.update_data(channel_chat_id=channel.chat_id, channel_username=f"@{channel.username}" if channel.username else channel.title)
    
    await state.set_state(CreateLottery.price)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆓 Бесплатно", callback_data=f"{CB_PREFIX}:price:0")],
        [InlineKeyboardButton(text="⭐ 1 Star", callback_data=f"{CB_PREFIX}:price:1")],
        [InlineKeyboardButton(text="⭐ 5 Stars", callback_data=f"{CB_PREFIX}:price:5")],
        [InlineKeyboardButton(text="⭐ 10 Stars", callback_data=f"{CB_PREFIX}:price:10")],
        [InlineKeyboardButton(text="⭐ 25 Stars", callback_data=f"{CB_PREFIX}:price:25")],
        [InlineKeyboardButton(text="⭐ 50 Stars", callback_data=f"{CB_PREFIX}:price:50")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:channel"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_PREFIX}:cancel")]
    ])
    await callback.message.edit_text(
        "💸 <b>Хорошо, теперь определим цену участия в лотерее:</b>\n\n"
        "📖 На выбор доступно два варианта: бесплатно и платно (Stars).",
        reply_markup=kb
    )


@admin_router.callback_query(F.data.startswith(f"{CB_PREFIX}:price:"))
async def process_price(callback: CallbackQuery, state: FSMContext):
    price = float(callback.data.split(":")[-1])
    await state.update_data(price=price)
    await state.set_state(CreateLottery.winning_tickets)
    
    data = await state.get_data()
    await callback.message.edit_text(
        f"🎯 <b>Теперь выберите выигрышный билет!</b>\n\n"
        f"Введите номер билета (от 1 до {data['tickets_count']}):\n"
        f"Если победителей несколько — укажите через запятую (например: 3,7,12)"
    )


@admin_router.message(CreateLottery.winning_tickets)
async def process_winning_tickets(message: Message, state: FSMContext):
    data = await state.get_data()
    total = data['tickets_count']
    winners_needed = data['winners_count']
    
    try:
        parts = [x.strip() for x in message.text.split(",")]
        numbers = [int(x) for x in parts if x.isdigit()]
        
        if len(numbers) != winners_needed:
            await message.answer(f"⚠️ Нужно указать ровно {winners_needed} номер(а). Попробуйте снова.")
            return
        
        for n in numbers:
            if not (1 <= n <= total):
                await message.answer(f"⚠️ Номер {n} вне диапазона (1-{total})")
                return
        
        if len(set(numbers)) != len(numbers):
            await message.answer("⚠️ Номера не должны повторяться")
            return
        
        await state.update_data(winning_numbers=numbers)
        await state.set_state(CreateLottery.preview)
        await show_preview(message, state)
    except ValueError:
        await message.answer("⚠️ Некорректный формат. Введите числа через запятую.")


async def show_preview(message_or_callback, state):
    data = await state.get_data()
    
    # Определяем message и функцию ответа
    if isinstance(message_or_callback, CallbackQuery):
        msg = message_or_callback.message
        answer_func = message_or_callback.message.edit_text
    else:
        msg = message_or_callback
        answer_func = message_or_callback.answer
    
    price_text = "Бесплатно" if data['price'] == 0 else f"{int(data['price'])} Stars"
    winning_text = ", ".join(f"#{n}" for n in data['winning_numbers'])
    
    text = (
        "👁‍🗨 <b>Предпросмотр лотереи:</b>\n\n"
        f"🎟 Количество билетов » <b>{data['tickets_count']}</b>\n"
        f"  ⤷ Победителей » <b>{data['winners_count']}</b>\n"
        f"  ⤷ Выигрышные билеты » <b>{winning_text}</b>\n\n"
        f"💸 Цена участия » <b>{price_text}</b>\n"
        f"🔔 Канал » <b>{data.get('channel_username', '—')}</b>\n\n"
        f"📖 Если всё верно, то подтвердите создание лотереи:"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить создание", callback_data=f"{CB_PREFIX}:finish")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:winning_tickets"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_PREFIX}:cancel")]
    ])
    
    await answer_func(text, reply_markup=kb)


@admin_router.callback_query(F.data == f"{CB_PREFIX}:finish")
async def finish_lottery(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    
    with get_session() as session:
        user = get_or_create_user(session, callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        
        lottery = Lottery(
            organizer_id=callback.from_user.id,
            title=data['title'],
            media_type=data.get('media_type'),
            media_file_id=data.get('media_file_id'),
            total_tickets=data['tickets_count'],
            winners_count=data['winners_count'],
            price_stars=data['price'],
            winning_numbers=json.dumps(data['winning_numbers']),
            channel_username=data.get('channel_username')
        )
        session.add(lottery)
        session.flush()
        
        for i in range(1, data['tickets_count'] + 1):
            session.add(Ticket(lottery_id=lottery.id, number=i))
        
        session.commit()
        lottery_id = lottery.id
        channel_chat_id = data.get('channel_chat_id')
    
    # Публикуем в канал
    try:
        lottery.chat_id = channel_chat_id
        sent_msg = await send_lottery_message(bot, lottery)
        
        with get_session() as session:
            lottery = session.query(Lottery).filter_by(id=lottery_id).first()
            lottery.message_id = sent_msg.message_id
            lottery.chat_id = str(sent_msg.chat.id)
            session.commit()
        
        await state.clear()
        await callback.message.edit_text(
            f"✅ <b>Лотерея успешно создана и опубликована!</b>\n\n"
            f"ID лотереи: <code>{lottery_id}</code>\n"
            f"Канал: {data.get('channel_username')}"
        )
    except TelegramForbiddenError:
        await callback.message.edit_text("❌ Бот не имеет прав на публикацию в этом канале.")
    except Exception as e:
        logger.error(f"Publish error: {e}")
        await callback.message.edit_text(f"❌ Ошибка публикации: {e}")


# ================= НАВИГАЦИЯ "НАЗАД" =================
@admin_router.callback_query(F.data.startswith(f"{CB_PREFIX}:back:"))
async def go_back(callback: CallbackQuery, state: FSMContext):
    step = callback.data.split(":")[-1]
    
    if step == "text":
        await state.set_state(CreateLottery.text)
        await callback.message.edit_text("✏️ <b>Отправьте текст лотереи:</b>")
    elif step == "tickets":
        await state.set_state(CreateLottery.tickets_count)
        buttons = []
        row = []
        for i in range(5, 101, 5):
            row.append(InlineKeyboardButton(text=str(i), callback_data=f"{CB_PREFIX}:tickets:{i}"))
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:text"),
                        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_PREFIX}:cancel")])
        await callback.message.edit_text("🎟 <b>Выберите количество билетов:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif step == "winners":
        await state.set_state(CreateLottery.winners_count)
        data = await state.get_data()
        max_w = min(10, data['tickets_count'])
        buttons = []
        row = []
        for i in range(1, max_w + 1):
            row.append(InlineKeyboardButton(text=str(i), callback_data=f"{CB_PREFIX}:winners:{i}"))
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:tickets"),
                        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_PREFIX}:cancel")])
        await callback.message.edit_text(f"⭐️ <b>Укажите количество победителей (1-{max_w}):</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif step == "channel":
        await state.set_state(CreateLottery.channel)
        with get_session() as session:
            channels = session.query(Channel).filter_by(owner_id=callback.from_user.id).all()
            buttons = []
            for ch in channels:
                buttons.append([InlineKeyboardButton(text=f"📢 {ch.title}", callback_data=f"{CB_PREFIX}:channel:{ch.id}")])
            buttons.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data=f"{CB_PREFIX}:addchannel")])
            buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:winners"),
                            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_PREFIX}:cancel")])
        await callback.message.edit_text("🔔 <b>Выберите канал:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif step == "winning_tickets":
        await state.set_state(CreateLottery.winning_tickets)
        data = await state.get_data()
        await callback.message.edit_text(f"🎯 <b>Введите выигрышный билет (1-{data['tickets_count']}):</b>")


@admin_router.callback_query(F.data == f"{CB_PREFIX}:cancel")
async def cancel_creation(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Создание лотереи отменено.")


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

        user = get_or_create_user(session, callback.from_user.id, callback.from_user.username, callback.from_user.first_name)

        # Проверка, не брал ли уже билет
        existing = session.query(Ticket).filter_by(lottery_id=lottery_id, user_id=callback.from_user.id).first()
        if existing:
            await callback.answer("⚠️ Ты уже выбирал билет в этой лотерее!", show_alert=True)
            return

        # Проверка, свободен ли билет
        ticket = session.query(Ticket).filter_by(lottery_id=lottery_id, number=ticket_num).first()
        if ticket.is_picked:
            await callback.answer("⚠️ Этот билет уже занят!", show_alert=True)
            return

        # Оплата
        if lottery.price_stars > 0:
            if user.balance_stars < lottery.price_stars:
                await callback.answer(f"⚠️ Недостаточно Stars! Нужно {lottery.price_stars}, у тебя {user.balance_stars}", show_alert=True)
                return
            user.balance_stars -= lottery.price_stars
            user.total_spent += lottery.price_stars
            org = session.query(User).filter_by(id=lottery.organizer_id).first()
            if org:
                org.balance_stars += lottery.price_stars

        # Фиксация билета
        ticket.user_id = callback.from_user.id
        ticket.is_picked = True
        ticket.picked_at = datetime.utcnow()
        
        winners = json.loads(lottery.winning_numbers)
        is_winner = ticket_num in winners
        
        if is_winner:
            ticket.is_winner = True
            user.total_won += 1
        
        session.commit()

    # Обновляем кнопки в канале
    await update_lottery_message(bot, lottery)
    
    if is_winner:
        await callback.answer("🎉 ПОБЕДА! 🟢", show_alert=True)
    else:
        await callback.answer("😔 Промах... 🔴", show_alert=True)


async def update_lottery_message(bot: Bot, lottery: Lottery):
    """Полностью обновляет сообщение лотереи (текст + кнопки)"""
    if not lottery.chat_id or not lottery.message_id:
        return
    
    text = (
        f"🎉 <b>{lottery.title}</b>\n\n"
        f"🎟 Билетов: <b>{lottery.total_tickets}</b>\n"
        f"🏆 Победителей: <b>{lottery.winners_count}</b>\n"
        f"💰 Цена: <b>{'Бесплатно' if lottery.price_stars == 0 else f'{int(lottery.price_stars)} Stars'}</b>\n"
        f"🔔 Канал: <b>{lottery.channel_username}</b>\n\n"
        f"👇 <b>Выбери билет и испытай удачу!</b>"
    )
    
    with get_session() as session:
        tickets = session.query(Ticket).filter_by(lottery_id=lottery.id).all()
        buttons = []
        row = []
        for t in tickets:
            if t.is_picked:
                if t.is_winner:
                    btn_text = f"🟢 {t.number}"
                else:
                    btn_text = f"🔴 {t.number}"
                cb_data = None
            else:
                btn_text = f"🎟️ {t.number}"
                cb_data = f"{CB_PREFIX}:ticket:{lottery.id}:{t.number}" if lottery.is_active else None
            row.append(InlineKeyboardButton(text=btn_text, callback_data=cb_data))
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    try:
        if lottery.media_type and lottery.media_file_id:
            # Для медиа нужно использовать edit_caption + edit_reply_markup
            await bot.edit_message_caption(chat_id=lottery.chat_id, message_id=lottery.message_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
        else:
            await bot.edit_message_text(text=text, chat_id=lottery.chat_id, message_id=lottery.message_id, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        logger.warning(f"Edit message error: {e}")


# ================= АККАУНТ =================
@account_router.message(Command("account"))
async def cmd_account(message: Message):
    with get_session() as session:
        user = get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.first_name)
        session.commit()
        
        total_tickets = session.query(Ticket).filter_by(user_id=user.id).count()
        won_tickets = session.query(Ticket).filter_by(user_id=user.id, is_winner=True).count()
        referrals_count = session.query(User).filter_by(referred_by=user.id).count()

    text = (
        f"👤 <b>Твой аккаунт</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"💰 Баланс: <b>{user.balance_stars:.1f} Stars</b>\n"
        f"🎟 Всего билетов: <b>{total_tickets}</b>\n"
        f"🏆 Побед: <b>{won_tickets}</b>\n"
        f"📉 Потрачено: <b>{user.total_spent:.1f} Stars</b>\n"
        f"👥 Рефералов: <b>{referrals_count}</b>\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Вывести Stars", callback_data=f"{CB_PREFIX}:withdraw")],
        [InlineKeyboardButton(text="📜 История", callback_data=f"{CB_PREFIX}:history")],
        [InlineKeyboardButton(text="🔗 Реферальная ссылка", callback_data=f"{CB_PREFIX}:reflink")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"{CB_PREFIX}:settings")]
    ])
    await message.answer(text, reply_markup=kb)


@account_router.callback_query(F.data == f"{CB_PREFIX}:withdraw")
async def withdraw_stars(callback: CallbackQuery):
    with get_session() as session:
        user = session.query(User).filter_by(id=callback.from_user.id).first()
        if not user or user.balance_stars < 10:
            await callback.answer("⚠️ Минимум 10 Stars для вывода", show_alert=True)
            return
        user.balance_stars -= 10
        withdrawal = Withdrawal(user_id=user.id, amount=10, status="pending")
        session.add(withdrawal)
    await callback.message.answer(f"✅ Заявка на вывод 10 Stars создана!")


@account_router.callback_query(F.data == f"{CB_PREFIX}:history")
async def show_history(callback: CallbackQuery):
    with get_session() as session:
        tickets = session.query(Ticket).filter_by(user_id=callback.from_user.id).order_by(Ticket.picked_at.desc()).limit(10).all()
        if not tickets:
            await callback.answer("📜 История пуста", show_alert=True)
            return
        text = "<b>Последние 10 билетов:</b>\n\n"
        for t in tickets:
            status = "🟢 ПОБЕДА" if t.is_winner else "🔴 Промах"
            text += f"🎟 #{t.number} — {status} | Лотерея #{t.lottery_id}\n"
    await callback.message.edit_text(text)


@account_router.callback_query(F.data == f"{CB_PREFIX}:reflink")
async def show_reflink(callback: CallbackQuery, bot: Bot):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{callback.from_user.id}"
    await callback.message.answer(f"🔗 <b>Ваша реферальная ссылка:</b>\n\n<code>{link}</code>")


# ================= НАСТРОЙКИ =================
@account_router.callback_query(F.data == f"{CB_PREFIX}:settings")
async def show_settings(callback: CallbackQuery):
    with get_session() as session:
        user = session.query(User).filter_by(id=callback.from_user.id).first()
        lang = user.language if user else "ru"
        notify = user.notify_referrals if user else True
    
    notify_text = "✅ Вкл" if notify else "❌ Выкл"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🌐 Язык: {lang.upper()}", callback_data=f"{CB_PREFIX}:lang")],
        [InlineKeyboardButton(text=f"🔔 Уведомления о рефералах: {notify_text}", callback_data=f"{CB_PREFIX}:toggle_notify")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back_to_account")]
    ])
    await callback.message.edit_text("⚙️ <b>Настройки</b>", reply_markup=kb)


@account_router.callback_query(F.data == f"{CB_PREFIX}:lang")
async def change_language(callback: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data=f"{CB_PREFIX}:setlang:ru")],
        [InlineKeyboardButton(text="🇬🇧 English", callback_data=f"{CB_PREFIX}:setlang:en")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:settings")]
    ])
    await callback.message.edit_text("🌐 <b>Выберите язык:</b>", reply_markup=kb)


@account_router.callback_query(F.data.startswith(f"{CB_PREFIX}:setlang:"))
async def set_language(callback: CallbackQuery):
    lang = callback.data.split(":")[-1]
    with get_session() as session:
        user = session.query(User).filter_by(id=callback.from_user.id).first()
        user.language = lang
    await callback.answer(f"✅ Язык изменён на {lang.upper()}")
    await show_settings(callback)


@account_router.callback_query(F.data == f"{CB_PREFIX}:toggle_notify")
async def toggle_notify(callback: CallbackQuery):
    with get_session() as session:
        user = session.query(User).filter_by(id=callback.from_user.id).first()
        user.notify_referrals = not user.notify_referrals
    await callback.answer("✅ Настройки обновлены")
    # Перерисовываем меню настроек
    await show_settings(callback)


@account_router.callback_query(F.data == f"{CB_PREFIX}:back_to_account")
async def back_to_account(callback: CallbackQuery):
    with get_session() as session:
        user = session.query(User).filter_by(id=callback.from_user.id).first()
        total_tickets = session.query(Ticket).filter_by(user_id=user.id).count()
        won_tickets = session.query(Ticket).filter_by(user_id=user.id, is_winner=True).count()
        referrals_count = session.query(User).filter_by(referred_by=user.id).count()

    text = (
        f"👤 <b>Твой аккаунт</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"💰 Баланс: <b>{user.balance_stars:.1f} Stars</b>\n"
        f"🎟 Всего билетов: <b>{total_tickets}</b>\n"
        f"🏆 Побед: <b>{won_tickets}</b>\n"
        f"📉 Потрачено: <b>{user.total_spent:.1f} Stars</b>\n"
        f"👥 Рефералов: <b>{referrals_count}</b>\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Вывести Stars", callback_data=f"{CB_PREFIX}:withdraw")],
        [InlineKeyboardButton(text="📜 История", callback_data=f"{CB_PREFIX}:history")],
        [InlineKeyboardButton(text="🔗 Реферальная ссылка", callback_data=f"{CB_PREFIX}:reflink")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"{CB_PREFIX}:settings")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)


# ================= ЗАПУСК =================
async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_routers(admin_router, lottery_router, account_router, settings_router)
    
    logger.info("Бот запущен...")
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
