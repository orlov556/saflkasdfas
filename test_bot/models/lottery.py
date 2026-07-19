from sqlalchemy import Column, Integer, String, BigInteger, Boolean, Text, ARRAY, Float, ForeignKey, DateTime
from sqlalchemy.ext.mutable import MutableList
from database import Base
from datetime import datetime

class Lottery(Base):
    __tablename__ = "lotteries"
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, nullable=False)
    media_type = Column(String(20), nullable=False)
    media_id = Column(String(255), nullable=False)
    text = Column(Text, nullable=False)
    total_tickets = Column(Integer, nullable=False)
    winning_numbers = Column(MutableList.as_mutable(ARRAY(Integer)), nullable=False)
    price_type = Column(String(50), default="free")
    price_amount = Column(Float, default=0.0)
    subscriptions = Column(MutableList.as_mutable(ARRAY(String)), default=[])
    premium_only = Column(Boolean, default=False)
    boost_enabled = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    is_finished = Column(Boolean, default=False)
    start_time = Column(DateTime, nullable=False)
    created_by = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

class Ticket(Base):
    __tablename__ = "tickets"
    
    id = Column(Integer, primary_key=True)
    lottery_id = Column(Integer, ForeignKey("lotteries.id"), nullable=False)
    ticket_number = Column(Integer, nullable=False)
    user_id = Column(BigInteger, nullable=True)
    username = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class LotteryWinner(Base):
    __tablename__ = "lottery_winners"
    
    id = Column(Integer, primary_key=True)
    lottery_id = Column(Integer, ForeignKey("lotteries.id"), nullable=False)
    user_id = Column(BigInteger, nullable=False)
    ticket_number = Column(Integer, nullable=False)
    prize = Column(String(255), nullable=True)
    won_at = Column(DateTime, default=datetime.utcnow)