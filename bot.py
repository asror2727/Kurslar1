#bot.py
# -*- coding: utf-8 -*-
"""
Valyuta konvertatsiya boti — bitta faylda, ishga tayyor.

FOYDALANISH:
1. Pastdagi BOT_TOKEN o'rniga o'z tokeningizni yozing (yoki Render'da
   Environment Variable qilib BOT_TOKEN nomi bilan qo'shing).
2. ADMIN_IDS ichiga o'z Telegram ID'ingizni yozing.
3. Ishga tushiring: python bot.py
"""
import os
import re
import asyncio
import logging
from datetime import datetime
from typing import Optional, Tuple, List

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

# =========================================================
#                      SOZLAMALAR
# =========================================================

# Tokenni shu yerga qo'ying (yoki Render Environment Variable: BOT_TOKEN)
BOT_TOKEN = os.getenv("BOT_TOKEN", "8961204603:AAETpZdTf4B6OHQKIHVYGChuj5LMqXfivrE")

# O'z Telegram ID'ingizni shu yerga yozing (yoki Render: ADMIN_IDS=111,222)
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "8856763799").split(",") if x.strip()]

# Render bepul Web Service portni shu orqali beradi
PORT = int(os.getenv("PORT", "10000"))

# Boshlang'ich kurslar (1 birlik = necha so'm)
RATES = {
    "USD": 11974.0,
    "RUB": 154.82,
    "TON": 21314.0,
    "STARS": 240.0,
    "UZS": 1.0,
}
LAST_UPDATED = datetime.now().strftime("%H:%M")

UPDATE_INTERVAL_SECONDS = 60  # kurslar har 60 soniyada yangilanadi

CBU_API_URL = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/"
COINGECKO_TON_URL = "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# =========================================================
#                 KURSLARNI YANGILASH
# =========================================================

async def fetch_cbu(session: aiohttp.ClientSession):
    try:
        async with session.get(CBU_API_URL, timeout=10) as resp:
            data = await resp.json()
            for item in data:
                code = item.get("Ccy")
                rate = item.get("Rate")
                if code in ("USD", "RUB") and rate:
                    RATES[code] = float(rate)
    except Exception as e:
        logger.warning(f"CBU kursini olishda xato: {e}")


async def fetch_ton(session: aiohttp.ClientSession):
    try:
        async with session.get(COINGECKO_TON_URL, timeout=10) as resp:
            data = await resp.json()
            ton_usd = data.get("the-open-network", {}).get("usd")
            if ton_usd and RATES.get("USD"):
                RATES["TON"] = float(ton_usd) * RATES["USD"]
    except Exception as e:
        logger.warning(f"TON kursini olishda xato: {e}")


async def refresh_rates_loop():
    global LAST_UPDATED
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                await asyncio.gather(fetch_cbu(session), fetch_ton(session))
            LAST_UPDATED = datetime.now().strftime("%H:%M")
            logger.info(f"Kurslar yangilandi: {RATES}")
        except Exception as e:
            logger.error(f"Kurs yangilashda xato: {e}")
        await asyncio.sleep(UPDATE_INTERVAL_SECONDS)


# =========================================================
#                 XABARNI PARSE QILISH
# =========================================================

ALIASES = {
    "USD": {"usd", "dollar", "dollor", "dollars", "$"},
    "RUB": {"rub", "rubl", "rubli", "rubles", "ruble"},
    "TON": {"ton", "tons"},
    "STARS": {"stars", "star", "stras", "★", "⭐"},
    "UZS": {"som", "so'm", "sum", "sўm", "uzs", "soum", "so’m"},
}
WORD_TO_CODE = {}
for code, words in ALIASES.items():
    for w in words:
        WORD_TO_CODE[w.lower()] = code


def normalize_currency(word: str) -> Optional[str]:
    if not word:
        return None
    return WORD_TO_CODE.get(word.strip().lower().strip(".,!?"))


def clean_number(raw: str) -> Optional[float]:
    raw = raw.replace("$", "").replace(",", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


ParsedLine = Tuple[float, str, Optional[str]]


def parse_line(line: str) -> Optional[ParsedLine]:
    line = line.strip()
    if not line:
        return None
    tokens = line.split()
    if not tokens:
        return None

    first = tokens[0]
    rest = tokens[1:]

    if first.startswith("$"):
        amount = clean_number(first)
        source = "USD"
    else:
        amount = clean_number(first)
        source = None

    if amount is None:
        return None

    if source is None:
        if not rest:
            return None
        source = normalize_currency(rest[0])
        rest = rest[1:]

    if source is None:
        return None

    target = normalize_currency(rest[0]) if rest else None
    return (amount, source, target)


def parse_message(text: str) -> List[ParsedLine]:
    results = []
    for line in text.splitlines():
        parsed = parse_line(line)
        if parsed:
            results.append(parsed)
    return results


# =========================================================
#                 KONVERTATSIYA / FORMAT
# =========================================================

CURRENCY_EMOJI = {"USD": "❤️", "RUB": "👍", "TON": "💕", "STARS": "⭐", "UZS": "💰"}
DECIMALS = {"USD": 2, "RUB": 2, "TON": 4, "STARS": 0, "UZS": 0}


def to_som(amount: float, source: str) -> float:
    rate = RATES.get(source)
    return amount * rate if rate else 0.0


def from_som(som_amount: float, target: str) -> float:
    rate = RATES.get(target)
    return som_amount / rate if rate else 0.0


def fmt_for(code: str, value: float) -> str:
    decimals = DECIMALS.get(code, 2)
    if decimals == 0:
        return f"{value:,.0f}"
    text = f"{value:,.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def emoji_for(code: str) -> str:
    return CURRENCY_EMOJI.get(code, "💠")


def format_direct_conversion(amount: float, source: str, target: str) -> str:
    som = to_som(amount, source)
    result = from_som(som, target)
    return (
        f"{emoji_for(source)} {fmt_for(source, amount)} {source} "
        f"→ {fmt_for(target, result)} {target}\n\n"
        f"Kurs: {LAST_UPDATED}"
    )


def format_to_som(amount: float, source: str) -> str:
    som = to_som(amount, source)
    return (
        f"{emoji_for(source)} {fmt_for(source, amount)} {source} "
        f"→ {fmt_for('UZS', som)} so'm\n\n"
        f"Kurs: {LAST_UPDATED}"
    )


def format_summary(parsed_lines: list) -> str:
    total_som = sum(to_som(a, s) for a, s, t in parsed_lines)
    usd = from_som(total_som, "USD")
    stars = from_som(total_som, "STARS")
    ton = from_som(total_som, "TON")
    lines = [
        f"💖 {fmt_for('USD', usd)} USD  →  {fmt_for('UZS', total_som)} so'm",
        f"⭐ {fmt_for('STARS', stars)} Stars",
        f"💕 {fmt_for('TON', ton)} TON",
        f"❤️ {fmt_for('USD', usd)} USD",
        "",
        f"Kurs: {LAST_UPDATED}",
    ]
    return "\n".join(lines)


# =========================================================
#                    TELEGRAM HANDLERLAR
# =========================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    text = (
        "Salom! 👋\n\n"
        "Men valyuta konvertatsiya botiman.\n\n"
        "Hozirgi kurslar:\n"
        f"❤️ 1 USD — {fmt_for('UZS', RATES['USD'])} so'm\n"
        f"👍 1 RUB — {fmt_for('UZS', RATES['RUB'])} so'm\n"
        f"💕 1 TON — {fmt_for('UZS', RATES['TON'])} so'm\n"
        f"⭐ 1 Stars — {fmt_for('UZS', RATES['STARS'])} so'm\n\n"
        "Qanday ishlatish:\n"
        "• 1 ton — necha so'm\n"
        "• 100 stars — necha so'm\n"
        "• 1 ton usd — TON dan USD ga\n"
        "• 50000 som ton — so'mdan TON ga\n"
        "• $10 — USD dan so'mga\n\n"
        "Bir nechta qator yozsangiz, hammasini yig'ib umumiy natijani chiqarib beraman."
    )
    await message.answer(text)


@dp.message(Command("setstars"))
async def cmd_set_stars(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Bu buyruq faqat admin uchun.")
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Format: /setstars 240")
        return
    try:
        value = float(parts[1])
    except ValueError:
        await message.answer("Son noto'g'ri kiritildi.")
        return
    global LAST_UPDATED
    RATES["STARS"] = value
    LAST_UPDATED = datetime.now().strftime("%H:%M")
    await message.answer(f"⭐ Stars kursi yangilandi: 1 Stars = {fmt_for('UZS', value)} so'm")


@dp.message(Command("kurs"))
async def cmd_kurs(message: Message):
    await cmd_start(message)


@dp.message(F.text)
async def handle_text(message: Message):
    parsed = parse_message(message.text)
    if not parsed:
        return

    if len(parsed) == 1:
        amount, source, target = parsed[0]
        if target:
            reply = format_direct_conversion(amount, source, target)
        else:
            reply = format_to_som(amount, source)
    else:
        reply = format_summary(parsed)

    await message.reply(reply)


# =========================================================
#          RENDER UCHUN KEEP-ALIVE HTTP SERVER
# =========================================================

async def handle_ping(request):
    return web.Response(text="Bot ishlayapti ✅")


async def start_keepalive_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Keep-alive server {PORT}-portda ishga tushdi.")


# =========================================================
#                        ISHGA TUSHIRISH
# =========================================================

async def main():
    asyncio.create_task(refresh_rates_loop())
    await start_keepalive_server()
    logger.info("Bot ishga tushdi.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
