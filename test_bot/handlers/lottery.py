# handlers/lottery.py
import json
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from models.database import get_session, Lottery, Ticket, User
from config import CB_PREFIX
from services.payments import credit_organizer

router = Router()


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:ticket:"))
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
    
    existing = session.query(Ticket).filter_by(
        lottery_id=lottery_id,
        user_id=callback.from_user.id
    ).first()
    
    if existing:
        await callback.answer("Ты уже выбирал билет!", show_alert=True)
        session.close()
        return
    
    ticket = session.query(Ticket).filter_by(
        lottery_id=lottery_id,
        number=ticket_num
    ).first()
    
    if ticket.is_picked:
        await callback.answer("Этот билет уже занят!", show_alert=True)
        session.close()
        return
    
    if lottery.price_stars > 0:
        pass
    
    if not user:
        user = User(
            id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name
        )
        session.add(user)
    
    ticket.user_id = callback.from_user.id
    ticket.is_picked = True
    ticket.picked_at = datetime.utcnow()
    
    winners = json.loads(lottery.winning_numbers)
    is_winner = ticket_num in winners
    
    if is_winner:
        ticket.is_winner = True
        user.total_won += 1
        
        picked_winners = session.query(Ticket).filter_by(
            lottery_id=lottery_id, is_winner=True
        ).count()
        if picked_winners >= lottery.winners_count:
            lottery.is_active = False
    
    session.commit()
    
    await update_lottery_buttons(bot, lottery, session)
    
    if is_winner:
        await callback.answer("ПОБЕДА! Ты выбрал выигрышный билет!", show_alert=True)
        await callback.message.reply(
            f"<b>Победитель!</b>\n"
            f"Пользователь {callback.from_user.mention_html()} выбрал билет #{ticket_num}\n"
            f"Лотерея: {lottery.title}",
            parse_mode="HTML"
        )
    else:
        await callback.answer("Промах... Попробуй в другой раз!", show_alert=True)
    
    if lottery.price_stars > 0:
        credit_organizer(lottery_id, lottery.price_stars)
    
    session.close()


async def update_lottery_buttons(bot: Bot, lottery: Lottery, session):
    tickets = session.query(Ticket).filter_by(lottery_id=lottery.id).all()
    
    buttons = []
    row = []
    
    for t in tickets:
        if t.is_picked:
            if t.is_winner:
                text = f"[WIN] #{t.number}"
            else:
                text = f"[X] #{t.number}"
        else:
            text = f"#{t.number}"
        
        row.append(InlineKeyboardButton(
            text=text,
            callback_data=f"{CB_PREFIX}:ticket:{lottery.id}:{t.number}"
        ))
        
        if len(row) == 5:
            buttons.append(row)
            row = []
    
    if row:
        buttons.append(row)
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    try:
        await bot.edit_message_reply_markup(
            chat_id=lottery.chat_id,
            message_id=lottery.message_id,
            reply_markup=kb
        )
    except TelegramBadRequest:
        pass
