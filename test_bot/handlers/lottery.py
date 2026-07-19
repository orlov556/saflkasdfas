from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from services.lottery import LotteryService
from models.channel import Channel
from datetime import datetime, timedelta

router = Router(name="lottery")

class CreateLotteryStates(StatesGroup):
    waiting_for_channel = State()
    waiting_for_media = State()
    waiting_for_text = State()
    waiting_for_tickets = State()
    waiting_for_winning_numbers = State()
    waiting_for_start_time = State()
    waiting_for_price = State()
    waiting_for_price_amount = State()
    waiting_for_subscriptions = State()
    waiting_for_premium_only = State()
    waiting_for_boost_enabled = State()

@router.message(Command("new_lot"))
async def new_lot_command(message: Message, state: FSMContext, session: AsyncSession):
    await start_lottery_creation(message, state, session)

@router.message(Command("create_lottery"))
async def create_lottery_command(message: Message, state: FSMContext, session: AsyncSession):
    await start_lottery_creation(message, state, session)

async def start_lottery_creation(message: Message, state: FSMContext, session: AsyncSession):
    channels = await session.execute(select(Channel))
    channels = channels.scalars().all()
    
    if not channels:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить канал", callback_data="add_channel")],
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_channels")]
            ]
        )
        await message.answer(
            "📢 **У вас нет добавленных каналов.**\n\n"
            "Добавьте канал командой /add_channel @username",
            reply_markup=kb
        )
        return
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"📌 {ch.title}", callback_data=f"channel_{ch.id}")]
            for ch in channels
        ] + [
            [InlineKeyboardButton(text="➕ Добавить канал", callback_data="add_channel")]
        ]
    )
    await message.answer(
        "📢 **Выберите канал для публикации лотереи:**",
        reply_markup=kb
    )
    await state.set_state(CreateLotteryStates.waiting_for_channel)

@router.callback_query(F.data == "add_channel")
async def add_channel_callback(callback: CallbackQuery):
    await callback.message.answer(
        "📌 **Чтобы добавить канал:**\n\n"
        "Напишите команду: /add_channel @username"
    )
    await callback.answer()

@router.callback_query(F.data == "refresh_channels")
async def refresh_channels(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await start_lottery_creation(callback.message, state, session)
    await callback.answer()

@router.callback_query(F.data.startswith("channel_"))
async def select_channel(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    channel_id = int(callback.data.split("_")[1])
    channel = await session.get(Channel, channel_id)
    await state.update_data(channel=channel.chat_id, channel_name=channel.title)
    
    await callback.message.edit_text(
        "🖼 **Загрузите медиа для лотереи**\n\n"
        "Можно отправить:\n"
        "• Фото\n"
        "• Видео\n"
        "• GIF-анимацию\n\n"
        "Или отправьте текст 'нет' чтобы пропустить (только текст)"
    )
    await state.set_state(CreateLotteryStates.waiting_for_media)
    await callback.answer()

@router.message(CreateLotteryStates.waiting_for_media)
async def process_media(message: Message, state: FSMContext):
    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        media_type = "video"
    elif message.animation:
        file_id = message.animation.file_id
        media_type = "gif"
    elif message.text and message.text.lower() == "нет":
        file_id = None
        media_type = "none"
    else:
        await message.answer(
            "❌ Пожалуйста, отправьте:\n"
            "• Фото\n"
            "• Видео\n"
            "• GIF-анимацию\n"
            "• Или 'нет' чтобы пропустить"
        )
        return
    
    await state.update_data(media_type=media_type, media_id=file_id)
    await message.answer(
        "✏️ **Напишите текст лотереи**\n\n"
        "💎 Используйте любые эмодзи:\n"
        "🎉 ✨ ⭐️ 🎁 💫 🌟\n\n"
        "Пример:\n"
        "🎉 РОЗЫГРЫШ iPhone 15 PRO MAX 🎉\n"
        "✨ Приз: iPhone 15 Pro Max 256GB\n"
        "⭐️ Участие: бесплатное\n"
        "🔥 Удачи!"
    )
    await state.set_state(CreateLotteryStates.waiting_for_text)

@router.message(CreateLotteryStates.waiting_for_text)
async def process_text(message: Message, state: FSMContext):
    await state.update_data(text=message.html_text)
    await message.answer(
        "🔢 **Сколько билетов будет в лотерее?**\n"
        "Введите число от 5 до 20 (для теста):"
    )
    await state.set_state(CreateLotteryStates.waiting_for_tickets)

@router.message(CreateLotteryStates.waiting_for_tickets)
async def process_tickets(message: Message, state: FSMContext):
    try:
        count = int(message.text)
        if count < 3 or count > 20:
            await message.answer("❌ Для теста используй от 3 до 20.")
            return
    except ValueError:
        await message.answer("❌ Введите число.")
        return
    
    await state.update_data(tickets=count)
    await message.answer(
        f"🎯 **Введите выигрышные номера** (от 1 до {count}) через запятую.\n\n"
        f"Пример: `7, 42, 99`"
    )
    await state.set_state(CreateLotteryStates.waiting_for_winning_numbers)

@router.message(CreateLotteryStates.waiting_for_winning_numbers)
async def process_winning_numbers(message: Message, state: FSMContext):
    data = await state.get_data()
    total = data["tickets"]
    try:
        numbers = [int(x.strip()) for x in message.text.split(",")]
        if not all(1 <= n <= total for n in numbers):
            await message.answer(f"❌ Все номера должны быть от 1 до {total}.")
            return
    except ValueError:
        await message.answer("❌ Введите числа через запятую.")
        return
    
    await state.update_data(winning=numbers)
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏰ Запустить сейчас", callback_data="start_now")],
            [InlineKeyboardButton(text="⏳ Через 1 минуту (тест)", callback_data="start_1min")],
            [InlineKeyboardButton(text="📅 Через 5 минут (тест)", callback_data="start_5min")]
        ]
    )
    await message.answer(
        "⏰ **Выберите время запуска лотереи:**",
        reply_markup=kb
    )
    await state.set_state(CreateLotteryStates.waiting_for_start_time)

@router.callback_query(F.data == "start_now")
async def start_now(callback: CallbackQuery, state: FSMContext):
    await state.update_data(start_time=datetime.utcnow())
    await ask_price(callback.message, state)
    await callback.answer()

@router.callback_query(F.data == "start_1min")
async def start_1min(callback: CallbackQuery, state: FSMContext):
    await state.update_data(start_time=datetime.utcnow() + timedelta(minutes=1))
    await ask_price(callback.message, state)
    await callback.answer()

@router.callback_query(F.data == "start_5min")
async def start_5min(callback: CallbackQuery, state: FSMContext):
    await state.update_data(start_time=datetime.utcnow() + timedelta(minutes=5))
    await ask_price(callback.message, state)
    await callback.answer()

async def ask_price(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🆓 Бесплатный", callback_data="price_free")],
            [InlineKeyboardButton(text="💰 Платный (тест 10 руб)", callback_data="price_paid")]
        ]
    )
    await message.answer(
        "💸 **Выберите тип участия:**",
        reply_markup=kb
    )
    await state.set_state(CreateLotteryStates.waiting_for_price)

@router.callback_query(F.data.startswith("price_"))
async def process_price(callback: CallbackQuery, state: FSMContext):
    price_type = callback.data.split("_")[1]
    if price_type == "paid":
        await state.update_data(price_type="paid", price_amount=10)
        await callback.message.edit_text("✅ Платная лотерея (10 руб/билет)")
        await ask_subscriptions(callback.message, state)
    else:
        await state.update_data(price_type="free", price_amount=0)
        await callback.message.edit_text("✅ Бесплатная лотерея.")
        await ask_subscriptions(callback.message, state)
    await callback.answer()

async def ask_subscriptions(message: Message, state: FSMContext):
    await message.answer(
        "📢 **Каналы для подписки** (через запятую).\n"
        "Если не требуется, отправьте: `-`\n\n"
        "Пример: `@channel1, @channel2`"
    )
    await state.set_state(CreateLotteryStates.waiting_for_subscriptions)

@router.message(CreateLotteryStates.waiting_for_subscriptions)
async def process_subscriptions(message: Message, state: FSMContext):
    text = message.text.strip()
    if text == "-":
        subs = []
    else:
        subs = [x.strip() for x in text.split(",") if x.strip()]
    
    await state.update_data(subscriptions=subs)
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data="premium_yes")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="premium_no")]
        ]
    )
    await message.answer(
        "🔒 **Требовать Premium-статус для участия?**",
        reply_markup=kb
    )
    await state.set_state(CreateLotteryStates.waiting_for_premium_only)

@router.callback_query(F.data.startswith("premium_"))
async def process_premium(callback: CallbackQuery, state: FSMContext):
    is_premium = callback.data == "premium_yes"
    await state.update_data(premium_only=is_premium)
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data="boost_yes")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="boost_no")]
        ]
    )
    await callback.message.edit_text(
        "🚀 **Включить буст-шанс?**\n"
        "Буст даёт дополнительный билет.",
        reply_markup=kb
    )
    await state.set_state(CreateLotteryStates.waiting_for_boost_enabled)

@router.callback_query(F.data.startswith("boost_"))
async def process_boost(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    boost_enabled = callback.data == "boost_yes"
    await state.update_data(boost_enabled=boost_enabled)
    
    data = await state.get_data()
    data["session"] = session
    data["created_by"] = callback.from_user.id
    
    await LotteryService.create_lottery(callback.bot, data)
    
    await callback.message.edit_text(
        f"✅ **Лотерея создана!**\n\n"
        f"📌 Канал: {data.get('channel_name', data['channel'])}\n"
        f"🎟️ Билетов: {data['tickets']}\n"
        f"🏆 Выигрышные номера: {', '.join(map(str, data['winning']))}\n"
        f"💰 Тип: {'Платный' if data['price_type'] == 'paid' else 'Бесплатный'}\n"
        f"🔒 Premium: {'✅ Да' if data.get('premium_only') else '❌ Нет'}\n"
        f"🚀 Буст: {'✅ Да' if data.get('boost_enabled') else '❌ Нет'}\n"
        f"⏰ Запуск: {data['start_time'].strftime('%d.%m.%Y %H:%M')} (UTC)\n\n"
        f"📢 Проверь канал и нажми на билет!"
    )
    await state.clear()
    await callback.answer()

# Обработчик кликов по билетам
@router.callback_query(F.data.startswith("ticket_"))
async def handle_ticket(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split("_")
    lottery_id = int(parts[1])
    ticket_number = int(parts[2])
    await LotteryService.select_ticket(callback, session, lottery_id, ticket_number)

# Обработчик уже занятых билетов
@router.callback_query(F.data.startswith("ticket_done_"))
async def ticket_done(callback: CallbackQuery):
    await callback.answer("🔒 Этот билет уже обработан", show_alert=True)