import sys
import os
import html
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
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Boolean, DateTime, ForeignKey, Float
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ")
CB_PREFIX = "lot"
ADMIN_CHAT_LINK = "https://t.me/your_admin_chat"

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
    media_type = Column(String, nullable=True)
    media_file_id = Column(String, nullable=True)
    total_tickets = Column(Integer)
    winners_count = Column(Integer, default=1)
    price_stars = Column(Float, default=0.0)
    winning_numbers = Column(String)
    channel_username = Column(String, nullable=True)
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


class Withdrawal(Base):
    __tablename__ = "withdrawals"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    amount = Column(Float)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)


# ================= FSM =================
class CreateLottery(StatesGroup):
    text = State()
    tickets_count = State()
    winners_count = State()
    channel = State()
    add_channel = State()
    price = State()
    preview = State()
    winning_tickets = State()


# ================= РОУТЕРЫ =================
main_router = Router()
lottery_router = Router()


# ================= УТИЛИТЫ =================
def get_or_create_user(session, user_id, username=None, first_name=None):
    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        user = User(id=user_id, username=username, first_name=first_name)
        session.add(user)
        session.flush()
    return user


async def send_lottery_message(bot: Bot, lottery_id: int, is_edit: bool = False, message_id: int = None, chat_id: str = None):
    with get_session() as session:
        lottery = session.query(Lottery).filter_by(id=lottery_id).first()
        if not lottery:
            return None
            
        tickets = session.query(Ticket).filter_by(lottery_id=lottery.id).all()
        
        safe_title = html.escape(lottery.title)
        safe_channel = html.escape(lottery.channel_username or "Не указан")
        
        text = (
            f" <b>{safe_title}</b>\n\n"
            f"🎟 Билетов: <b>{lottery.total_tickets}</b>\n"
            f" Победителей: <b>{lottery.winners_count}</b>\n"
            f"💰 Цена: <b>{'Бесплатно' if lottery.price_stars == 0 else f'{int(lottery.price_stars)} Stars'}</b>\n"
            f"🔔 Канал: <b>{safe_channel}</b>\n\n"
            f"👇 <b>Выбери билет и испытай удачу!</b>"
        )
        
        buttons = []
        row = []
        for t in tickets:
            if t.is_picked:
                btn_text = f"🟢 {t.number}" if t.is_winner else f"🔴 {t.number}"
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
        
        target_chat_id = chat_id or lottery.chat_id
        target_message_id = message_id or lottery.message_id

        if not target_chat_id:
            return None

        try:
            if is_edit and target_message_id:
                if lottery.media_type and lottery.media_file_id:
                    await bot.edit_message_caption(chat_id=target_chat_id, message_id=target_message_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
                else:
                    await bot.edit_message_text(text=text, chat_id=target_chat_id, message_id=target_message_id, reply_markup=kb, parse_mode=ParseMode.HTML)
                return type('obj', (object,), {'message_id': target_message_id, 'chat': type('obj', (object,), {'id': target_chat_id})})
            else:
                if lottery.media_type == "photo":
                    return await bot.send_photo(chat_id=target_chat_id, photo=lottery.media_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif lottery.media_type == "video":
                    return await bot.send_video(chat_id=target_chat_id, video=lottery.media_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif lottery.media_type == "animation":
                    return await bot.send_animation(chat_id=target_chat_id, animation=lottery.media_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
                else:
                    return await bot.send_message(chat_id=target_chat_id, text=text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except TelegramBadRequest as e:
            logger.error(f"Telegram API Error: {e}")
            return None


# ================= /start =================
@main_router.message(CommandStart())
async def cmd_start(message: Message):
    with get_session() as session:
        user = get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.first_name)
        session.commit()

    me = await message.bot.get_me()
    text = (
        "🎰 <b>Добро пожаловать в Лотерею!</b>\n\n"
        "📋 <b>Команды:</b>\n"
        "• /newlottery — создать лотерею\n"
        "• /account — ваш аккаунт\n"
        "• /help — помощь\n\n"
        f" <b>Ваша реферальная ссылка:</b>\n"
        f"<code>https://t.me/{me.username}?start=ref_{message.from_user.id}</code>"
    )
    await message.answer(text)


@main_router.message(Command("help"))
async def cmd_help(message: Message):
    text = "📖 <b>Помощь</b>\n\n• /newlottery — создать лотерею\n• /account — ваш аккаунт"
    await message.answer(text)


# ================= /account - ИСПРАВЛЕНО ОКОНЧАТЕЛЬНО =================
@main_router.message(Command("account"))
async def cmd_account(message: Message):
    try:
        with get_session() as session:
            user = get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.first_name)
            session.commit()
            
            total_tickets = session.query(Ticket).filter_by(user_id=user.id).count()
            won_tickets = session.query(Ticket).filter_by(user_id=user.id, is_winner=True).count()
            referrals_count = session.query(User).filter_by(referred_by=user.id).count()

        is_premium = "✅ Premium" if getattr(message.from_user, 'is_premium', False) else "❌ Нет"
        reg_date = user.created_at.strftime("%d.%m.%Y %H:%M") if user.created_at else "Неизвестно"

        text = (
            f"👤 <b>Ваш аккаунт:</b>\n\n"
            f"⚡ Подписка » {is_premium}\n\n"
            f"👤 UUID » <code>{user.id}</code>\n"
            f"  ⤷ Регистрация » <code>{reg_date}</code>\n\n"
            f"🏦 <b>Балансы:</b>\n"
            f"  ⤷ Звёзды » <b>{user.balance_stars:.2f}</b> | Удержание » <b>0.00</b>\n"
            f"   Доллары » <b>$0.00</b>\n"
            f"   Гемы » <b>0</b>\n\n"
            f"⭐️ История трат звёзд: <b>{user.total_spent:.2f}</b>\n\n"
            f"💬 <a href='{ADMIN_CHAT_LINK}'>Присоединяйтесь к админскому чату бота</a>.\n\n"
            f"📖 <b>Управление аккаунтом:</b>"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💸 Вывести Stars", callback_data=f"{CB_PREFIX}:withdraw")],
            [InlineKeyboardButton(text="📜 История", callback_data=f"{CB_PREFIX}:history")],
            [InlineKeyboardButton(text="🔗 Рефералы", callback_data=f"{CB_PREFIX}:reflink")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"{CB_PREFIX}:settings")]
        ])
        await message.answer(text, reply_markup=kb, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Account error: {e}")
        await message.answer("⚠️ Ошибка при загрузке аккаунта. Попробуйте позже.")


# Callbacks для account
@main_router.callback_query(F.data == f"{CB_PREFIX}:withdraw")
async def withdraw_stars(callback: CallbackQuery):
    with get_session() as session:
        user = session.query(User).filter_by(id=callback.from_user.id).first()
        if not user or user.balance_stars < 10:
            await callback.answer("⚠️ Минимум 10 Stars для вывода", show_alert=True)
            return
        user.balance_stars -= 10
        session.add(Withdrawal(user_id=user.id, amount=10, status="pending"))
    await callback.message.answer("✅ Заявка на вывод 10 Stars создана!")


@main_router.callback_query(F.data == f"{CB_PREFIX}:history")
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


@main_router.callback_query(F.data == f"{CB_PREFIX}:reflink")
async def show_reflink(callback: CallbackQuery):
    me = await callback.bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{callback.from_user.id}"
    await callback.message.answer(f" <b>Ваша реферальная ссылка:</b>\n\n<code>{link}</code>")


@main_router.callback_query(F.data == f"{CB_PREFIX}:settings")
async def show_settings(callback: CallbackQuery):
    with get_session() as session:
        user = session.query(User).filter_by(id=callback.from_user.id).first()
        notify = user.notify_referrals if user else True
    notify_text = "✅ Вкл" if notify else "❌ Выкл"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔔 Уведомления: {notify_text}", callback_data=f"{CB_PREFIX}:toggle_notify")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back_to_account")]
    ])
    await callback.message.edit_text("⚙️ <b>Настройки</b>", reply_markup=kb)


@main_router.callback_query(F.data == f"{CB_PREFIX}:toggle_notify")
async def toggle_notify(callback: CallbackQuery):
    with get_session() as session:
        user = session.query(User).filter_by(id=callback.from_user.id).first()
        user.notify_referrals = not user.notify_referrals
    await callback.answer("✅ Обновлено")
    await show_settings(callback)


@main_router.callback_query(F.data == f"{CB_PREFIX}:back_to_account")
async def back_to_account(callback: CallbackQuery):
    await cmd_account(callback.message)


# ================= СОЗДАНИЕ ЛОТЕРЕИ =================
@main_router.message(Command("newlottery"))
async def cmd_new_lottery(message: Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("⚠️ Только в ЛС!")
        return
    await state.set_state(CreateLottery.text)
    await message.answer("✏️ <b>Отправьте текст лотереи:</b>\n\n Можно прикрепить фото, видео, GIF.")


@main_router.message(CreateLottery.text)
async def process_text(message: Message, state: FSMContext):
    media_type = None
    file_id = None
    caption = message.caption or message.text or ""
    
    if message.photo: media_type, file_id = "photo", message.photo[-1].file_id
    elif message.video: media_type, file_id = "video", message.video.file_id
    elif message.animation: media_type, file_id = "animation", message.animation.file_id
    elif message.audio: media_type, file_id = "audio", message.audio.file_id
    elif message.document: media_type, file_id = "document", message.document.file_id
    
    if not caption and not media_type:
        await message.answer("⚠️ Отправьте текст или медиа!")
        return
    
    await state.update_data(title=caption or "Лотерея", media_type=media_type, media_file_id=file_id)
    await state.set_state(CreateLottery.tickets_count)
    
    buttons = []
    row = []
    for i in range(5, 101, 5):
        row.append(InlineKeyboardButton(text=str(i), callback_data=f"{CB_PREFIX}:tickets:{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row: buttons.append(row)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:text"), 
                    InlineKeyboardButton(text=" Удалить", callback_data=f"{CB_PREFIX}:cancel")])
    
    await message.answer(" <b>Выберите количество билетов:</b>", 
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@main_router.callback_query(F.data.startswith(f"{CB_PREFIX}:tickets:"))
async def process_tickets_count(callback: CallbackQuery, state: FSMContext):
    count = int(callback.data.split(":")[-1])
    await state.update_data(tickets_count=count)
    await state.set_state(CreateLottery.winners_count)
    
    # КАК НА СКРИНШОТЕ - ОДНА КНОПКА "По умолчанию: 1"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="По умолчанию: 1", callback_data=f"{CB_PREFIX}:winners:1")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:tickets"), 
         InlineKeyboardButton(text="Удалить", callback_data=f"{CB_PREFIX}:cancel")]
    ])
    
    await callback.message.edit_text(
        f"⭐️ <b>Отлично, теперь укажите количество победителей в лотерее (от 1 до {min(count, 100)}):</b>",
        reply_markup=kb
    )


@main_router.callback_query(F.data.startswith(f"{CB_PREFIX}:winners:"))
async def process_winners_count(callback: CallbackQuery, state: FSMContext):
    count = int(callback.data.split(":")[-1])
    await state.update_data(winners_count=count)
    await state.set_state(CreateLottery.channel)
    
    with get_session() as session:
        channels = session.query(Channel).filter_by(owner_id=callback.from_user.id).all()
        buttons = [[InlineKeyboardButton(text=f"📢 {ch.title}", callback_data=f"{CB_PREFIX}:channel:{ch.id}")] for ch in channels]
        buttons.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data=f"{CB_PREFIX}:addchannel")])
        buttons.append([InlineKeyboardButton(text="️ Назад", callback_data=f"{CB_PREFIX}:back:winners"), 
                        InlineKeyboardButton(text="Удалить", callback_data=f"{CB_PREFIX}:cancel")])
    
    await callback.message.edit_text("🔔 <b>Выберите канал для публикации:</b>", 
                                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@main_router.callback_query(F.data == f"{CB_PREFIX}:addchannel")
async def add_channel_prompt(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateLottery.add_channel)
    text = (
        " <b>Начнём подключение канала!</b>\n\n"
        "1. Отправьте @username канала или ссылку.\n"
        "2. Бот должен быть администратором канала.\n\n"
        "📖 <b>Отправьте канал:</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CB_PREFIX}:back:channel")]])
    await callback.message.edit_text(text, reply_markup=kb)


@main_router.message(CreateLottery.add_channel)
async def process_add_channel(message: Message, state: FSMContext, bot: Bot):
    text = message.text.strip() if message.text else ""
    forward = message.forward_from_chat if message.forward_from_chat else None
    chat_id, username, title = None, None, None
    
    if forward:
        chat_id, username, title = str(forward.id), forward.username or "", forward.title or "Канал"
    elif text.startswith("@") or "t.me/" in text:
        username = text.replace("@", "").replace("https://t.me/", "").split("/")[0]
        try:
            chat = await bot.get_chat(f"@{username}")
            chat_id, title = str(chat.id), chat.title or "Канал"
        except TelegramBadRequest:
            await message.answer("⚠️ Канал не найден.")
            return
    else:
        await message.answer("⚠️ Отправьте @username или ссылку.")
        return
    
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
        if member.status not in ["administrator", "creator"]:
            await message.answer("️ Бот не администратор канала!")
            return
    except TelegramBadRequest:
        await message.answer("⚠️ Бот не найден в канале.")
        return
    
    with get_session() as session:
        if not session.query(Channel).filter_by(owner_id=message.from_user.id, chat_id=chat_id).first():
            session.add(Channel(owner_id=message.from_user.id, chat_id=chat_id, username=username, title=title))
    
    await message.answer(f"✅ Канал <b>{title}</b> добавлен!")
    await state.set_state(CreateLottery.channel)
    await process_winners_count(callback, state)


@main_router.callback_query(F.data.startswith(f"{CB_PREFIX}:channel:"))
async def process_channel_choice(callback: CallbackQuery, state: FSMContext):
    ch_id = int(callback.data.split(":")[-1])
    with get_session() as session:
        channel = session.query(Channel).filter_by(id=ch_id, owner_id=callback.from_user.id).first()
        if channel:
            await state.update_data(channel_chat_id=channel.chat_id, channel_username=f"@{channel.username}" if channel.username else channel.title)
    
    await state.set_state(CreateLottery.price)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆓 Бесплатно", callback_data=f"{CB_PREFIX}:price:0")],
        [InlineKeyboardButton(text="⭐ 10 Stars", callback_data=f"{CB_PREFIX}:price:10")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:channel"), 
         InlineKeyboardButton(text="Удалить", callback_data=f"{CB_PREFIX}:cancel")]
    ])
    await callback.message.edit_text("💸 <b>Выберите цену участия:</b>", reply_markup=kb)


@main_router.callback_query(F.data.startswith(f"{CB_PREFIX}:price:"))
async def process_price(callback: CallbackQuery, state: FSMContext):
    await state.update_data(price=float(callback.data.split(":")[-1]))
    await state.set_state(CreateLottery.winning_tickets)
    data = await state.get_data()
    await callback.message.edit_text(
        f"🎯 <b>Введите номер выигрышного билета (1-{data['tickets_count']}):</b>\n"
        f"Если победителей несколько — через запятую (3,7,12)"
    )


@main_router.message(CreateLottery.winning_tickets)
async def process_winning_tickets(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        numbers = [int(x.strip()) for x in message.text.split(",") if x.strip().isdigit()]
        if len(numbers) != data['winners_count']:
            await message.answer(f"⚠️ Нужно {data['winners_count']} номер(а)!")
            return
        if any(not (1 <= n <= data['tickets_count']) for n in numbers):
            await message.answer(f"⚠️ Номера должны быть 1-{data['tickets_count']}")
            return
        
        await state.update_data(winning_numbers=numbers)
        await state.set_state(CreateLottery.preview)
        
        price_text = "Бесплатно" if data['price'] == 0 else f"{int(data['price'])} Stars"
        win_text = ", ".join(f"#{n}" for n in numbers)
        text = (
            f"👁‍🗨 <b>Предпросмотр:</b>\n\n"
            f"🎟 Билетов » <b>{data['tickets_count']}</b>\n"
            f"  ⤷ Победителей » <b>{data['winners_count']}</b>\n"
            f"  ⤷ Выигрышные » <b>{win_text}</b>\n\n"
            f"💸 Цена » <b>{price_text}</b>\n"
            f"🔔 Канал » <b>{data.get('channel_username', '—')}</b>\n\n"
            f"📖 Подтвердите создание:"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Завершить создание", callback_data=f"{CB_PREFIX}:finish")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{CB_PREFIX}:back:winning_tickets"), 
             InlineKeyboardButton(text="Удалить", callback_data=f"{CB_PREFIX}:cancel")]
        ])
        await message.answer(text, reply_markup=kb)
    except ValueError:
        await message.answer("⚠️ Введите числа через запятую.")


@main_router.callback_query(F.data == f"{CB_PREFIX}:finish")
async def finish_lottery(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    lottery_id = None
    
    with get_session() as session:
        lottery = Lottery(
            organizer_id=callback.from_user.id,
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
        lottery_id = lottery.id
        
        for i in range(1, data['tickets_count'] + 1):
            session.add(Ticket(lottery_id=lottery.id, number=i))
        session.commit()

    if lottery_id:
        try:
            sent_msg = await send_lottery_message(bot, lottery_id)
            if sent_msg:
                with get_session() as session:
                    lot = session.query(Lottery).filter_by(id=lottery_id).first()
                    lot.message_id = sent_msg.message_id
                    lot.chat_id = str(sent_msg.chat.id)
                await state.clear()
                await callback.message.edit_text(f"✅ <b>Лотерея создана!</b>\nID: <code>{lottery_id}</code>")
            else:
                await callback.message.edit_text("❌ Ошибка публикации.")
        except Exception as e:
            logger.error(f"Publish error: {e}")
            await callback.message.edit_text(f"❌ Ошибка: {e}")


@main_router.callback_query(F.data == f"{CB_PREFIX}:cancel")
async def cancel_creation(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Создание отменено.")


@main_router.callback_query(F.data.startswith(f"{CB_PREFIX}:back:"))
async def go_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Введите /newlottery для начала")


# ================= УЧАСТИЕ В ЛОТЕРЕЕ =================
@lottery_router.callback_query(F.data.startswith(f"{CB_PREFIX}:ticket:"))
async def pick_ticket(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    lottery_id = int(parts[2])
    ticket_num = int(parts[3])

    with get_session() as session:
        lottery = session.query(Lottery).filter_by(id=lottery_id).first()
        if not lottery or not lottery.is_active:
            await callback.answer("🚫 Завершена", show_alert=True)
            return

        user = get_or_create_user(session, callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        existing = session.query(Ticket).filter_by(lottery_id=lottery_id, user_id=callback.from_user.id).first()
        if existing:
            await callback.answer("⚠️ Вы уже брали билет!", show_alert=True)
            return

        ticket = session.query(Ticket).filter_by(lottery_id=lottery_id, number=ticket_num).first()
        if ticket.is_picked:
            await callback.answer("⚠️ Билет занят!", show_alert=True)
            return

        if lottery.price_stars > 0:
            if user.balance_stars < lottery.price_stars:
                await callback.answer(f"⚠️ Нужно {lottery.price_stars} Stars", show_alert=True)
                return
            user.balance_stars -= lottery.price_stars
            user.total_spent += lottery.price_stars
            org = session.query(User).filter_by(id=lottery.organizer_id).first()
            if org: org.balance_stars += lottery.price_stars

        ticket.user_id = callback.from_user.id
        ticket.is_picked = True
        ticket.picked_at = datetime.utcnow()
        
        winners = json.loads(lottery.winning_numbers)
        if ticket_num in winners:
            ticket.is_winner = True
            user.total_won += 1
        
        session.commit()

    await send_lottery_message(bot, lottery_id, is_edit=True, message_id=lottery.message_id, chat_id=lottery.chat_id)
    
    if ticket.is_winner:
        await callback.answer("🎉 ПОБЕДА! 🟢", show_alert=True)
    else:
        await callback.answer(" Промах... 🔴", show_alert=True)


# ================= ЗАПУСК =================
async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    
    # ВАЖНО: Все роутеры добавляем
    dp.include_routers(main_router, lottery_router)
    
    logger.info("Бот запущен...")
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
