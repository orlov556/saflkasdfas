# services/payments.py
from aiogram import Bot
from aiogram.types import LabeledPrice

from models.database import get_session, User, Lottery


async def process_star_payment(bot: Bot, user_id: int, lottery: Lottery) -> bool:
    prices = [LabeledPrice(label="Билет лотереи", amount=int(lottery.price_stars))]
    
    await bot.send_invoice(
        chat_id=user_id,
        title=f"Билет «{lottery.title}»",
        description=f"Билет №? из {lottery.total_tickets}",
        payload=f"lottery_{lottery.id}",
        provider_token="",
        currency="XTR",
        prices=prices
    )
    return True


async def credit_organizer(lottery_id: int, amount: float):
    session = get_session()
    lottery = session.query(Lottery).filter_by(id=lottery_id).first()
    if lottery:
        org = session.query(User).filter_by(id=lottery.organizer_id).first()
        if org:
            org.balance_stars += amount
            session.commit()
    session.close()
