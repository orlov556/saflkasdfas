from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models.lottery import Lottery, Ticket, LotteryWinner
from datetime import datetime
import asyncio

class LotteryService:
    
    @staticmethod
    async def create_lottery(bot: Bot, data: dict):
        session = data["session"]
        
        lottery = Lottery(
            chat_id=data["channel"],
            media_type=data.get("media_type", "none"),
            media_id=data.get("media_id"),
            text=data["text"],
            total_tickets=data["tickets"],
            winning_numbers=data["winning"],
            price_type=data["price_type"],
            price_amount=data.get("price_amount", 0),
            subscriptions=data.get("subscriptions", []),
            premium_only=data.get("premium_only", False),
            boost_enabled=data.get("boost_enabled", False),
            start_time=data["start_time"],
            created_by=data["created_by"]
        )
        session.add(lottery)
        await session.commit()
        await session.refresh(lottery)
        
        for i in range(1, data["tickets"] + 1):
            ticket = Ticket(lottery_id=lottery.id, ticket_number=i)
            session.add(ticket)
        await session.commit()
        
        await LotteryService.send_to_channel(bot, lottery)
        
        # Запускаем таймер для теста
        asyncio.create_task(LotteryService.schedule_finish(bot, lottery.id, data["start_time"], session))
        
        return lottery
    
    @staticmethod
    async def send_to_channel(bot: Bot, lottery):
        keyboard = LotteryService.build_ticket_keyboard(lottery.id, lottery.total_tickets)
        
        caption = lottery.text
        if lottery.premium_only:
            caption += "\n\n⭐ Только для Premium-пользователей"
        if lottery.subscriptions:
            caption += f"\n\n📢 Подписки: {', '.join(lottery.subscriptions)}"
        if lottery.price_type == "paid":
            caption += f"\n\n💰 Цена: {lottery.price_amount} ₽"
        
        caption += f"\n\n🎟️ Всего билетов: {lottery.total_tickets}"
        
        if lottery.media_type == "photo":
            await bot.send_photo(
                chat_id=lottery.chat_id,
                photo=lottery.media_id,
                caption=caption,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        elif lottery.media_type == "video":
            await bot.send_video(
                chat_id=lottery.chat_id,
                video=lottery.media_id,
                caption=caption,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        elif lottery.media_type == "gif":
            await bot.send_animation(
                chat_id=lottery.chat_id,
                animation=lottery.media_id,
                caption=caption,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await bot.send_message(
                chat_id=lottery.chat_id,
                text=caption,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    
    @staticmethod
    def build_ticket_keyboard(lottery_id: int, total: int):
        buttons = []
        row = []
        for i in range(1, total + 1):
            row.append(
                InlineKeyboardButton(
                    text=f"🎟️ {i}",
                    callback_data=f"ticket_{lottery_id}_{i}"
                )
            )
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    @staticmethod
    async def select_ticket(callback, session: AsyncSession, lottery_id: int, ticket_number: int):
        ticket = await session.execute(
            select(Ticket).where(
                Ticket.lottery_id == lottery_id,
                Ticket.ticket_number == ticket_number
            )
        )
        ticket = ticket.scalar_one_or_none()
        
        if ticket.user_id is not None:
            await callback.answer("❌ Билет занят!", show_alert=True)
            return
        
        lottery = await session.execute(
            select(Lottery).where(Lottery.id == lottery_id)
        )
        lottery = lottery.scalar_one_or_none()
        
        if not lottery or not lottery.is_active:
            await callback.answer("❌ Лотерея завершена!", show_alert=True)
            return
        
        if lottery.premium_only and not callback.from_user.is_premium:
            await callback.answer("🔒 Требуется Premium!", show_alert=True)
            return
        
        # Проверка подписок
        for sub in lottery.subscriptions:
            try:
                member = await callback.bot.get_chat_member(sub, callback.from_user.id)
                if member.status not in ["member", "administrator", "creator"]:
                    await callback.answer(f"❌ Подпишись на {sub}!", show_alert=True)
                    return
            except:
                pass
        
        # Занимаем билет
        ticket.user_id = callback.from_user.id
        ticket.username = callback.from_user.username or callback.from_user.first_name
        await session.commit()
        
        is_winner = ticket_number in lottery.winning_numbers
        
        if is_winner:
            winner = LotteryWinner(
                lottery_id=lottery.id,
                user_id=callback.from_user.id,
                ticket_number=ticket_number
            )
            session.add(winner)
            await session.commit()
            await callback.answer("🎉 ПОБЕДА!", show_alert=True)
            
            # Уведомление админу
            await bot.send_message(
                chat_id=lottery.created_by,
                text=f"🏆 Победитель! Билет #{ticket_number}\n@{callback.from_user.username or callback.from_user.first_name}"
            )
        else:
            await callback.answer("❌ Не угадал", show_alert=True)
        
        await LotteryService.update_ticket_status(callback, lottery.id, ticket_number, is_winner)
    
    @staticmethod
    async def update_ticket_status(callback, lottery_id: int, ticket_number: int, is_winner: bool):
        message = callback.message
        keyboard = message.reply_markup
        
        new_buttons = []
        for row in keyboard.inline_keyboard:
            new_row = []
            for btn in row:
                if btn.callback_data == f"ticket_{lottery_id}_{ticket_number}":
                    emoji = "🟩" if is_winner else "🟥"
                    new_row.append(
                        InlineKeyboardButton(
                            text=f"{emoji} {ticket_number}",
                            callback_data=f"ticket_done_{lottery_id}_{ticket_number}"
                        )
                    )
                else:
                    new_row.append(btn)
            new_buttons.append(new_row)
        
        new_keyboard = InlineKeyboardMarkup(inline_keyboard=new_buttons)
        await callback.message.edit_reply_markup(reply_markup=new_keyboard)
    
    @staticmethod
    async def schedule_finish(bot: Bot, lottery_id: int, start_time: datetime, session: AsyncSession):
        delay = (start_time - datetime.utcnow()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        
        # Завершаем
        async with session() as sess:
            lottery = await sess.get(Lottery, lottery_id)
            if lottery and lottery.is_active:
                lottery.is_active = False
                lottery.is_finished = True
                lottery.finished_at = datetime.utcnow()
                await sess.commit()
                
                winners = await sess.execute(
                    select(LotteryWinner).where(LotteryWinner.lottery_id == lottery_id)
                )
                winners = winners.scalars().all()
                
                if winners:
                    text = f"🏁 ЛОТЕРЕЯ ЗАВЕРШЕНА!\n\nПобедители:\n"
                    for w in winners:
                        text += f"• Билет #{w.ticket_number}\n"
                    await bot.send_message(chat_id=lottery.created_by, text=text)