# config.py
import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "твой_токен_сюда")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []

MIN_TICKETS = 5
MAX_TICKETS = 100
MAX_WINNERS = 100

CB_PREFIX = "lot"
CB_TICKET = f"{CB_PREFIX}:ticket"
CB_ACCOUNT = f"{CB_PREFIX}:account"
CB_WITHDRAW = f"{CB_PREFIX}:withdraw"
CB_ADD_BOT = f"{CB_PREFIX}:addbot"
