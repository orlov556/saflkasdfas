# handlers/account.py
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from models.database import get_session, User, Ticket
from config import CB_PREFIX

router = Router()


@router.message(Command("account"))
async def cmd_account(message: Message):
    session = get_session()
    user = session.query(User).filter_by(id=message.from_user.id).first()
    
    if not user:
        user = User(
            id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name
        )
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


@router.callback_query(F.data == f"{CB_PREFIX}:withdraw")
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


@router.callback_query(F.data == f"{CB_PREFIX}:history")
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
