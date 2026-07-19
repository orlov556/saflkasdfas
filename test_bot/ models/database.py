# models/database.py
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Boolean, DateTime, ForeignKey, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

Base = declarative_base()
engine = create_engine("sqlite:///lottery.db", echo=False)
Session = sessionmaker(bind=engine)


class User(Base):
    __tablename__ = "users"
    
    id = Column(BigInteger, primary_key=True)
    username = Column(String)
    first_name = Column(String)
    balance_stars = Column(Float, default=0.0)
    total_spent = Column(Float, default=0.0)
    total_won = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    tickets = relationship("Ticket", back_populates="user")
    organized_lotteries = relationship("Lottery", back_populates="organizer")


class Lottery(Base):
    __tablename__ = "lotteries"
    
    id = Column(Integer, primary_key=True)
    message_id = Column(BigInteger)
    chat_id = Column(BigInteger)
    organizer_id = Column(BigInteger, ForeignKey("users.id"))
    
    title = Column(String)
    total_tickets = Column(Integer)
    winners_count = Column(Integer, default=1)
    price_stars = Column(Float, default=0.0)
    winning_numbers = Column(String)
    
    require_premium = Column(Boolean, default=False)
    require_boost = Column(Boolean, default=False)
    require_subscription = Column(Boolean, default=False)
    subscription_chat_id = Column(BigInteger, nullable=True)
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    organizer = relationship("User", back_populates="organized_lotteries")
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


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    return Session()
