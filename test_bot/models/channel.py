from sqlalchemy import Column, Integer, String, BigInteger
from database import Base

class Channel(Base):
    __tablename__ = "channels"
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, unique=True, nullable=False)
    title = Column(String(255), nullable=False)
    username = Column(String(255), nullable=True)