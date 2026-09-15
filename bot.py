import asyncio
import aiohttp
import aiosqlite
import os
import re
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = "bot.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ─── База данных ───────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                status TEXT DEFAULT 'free',  -- free / busy
                user_id INTEGER DEFAULT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tag TEXT,
                name TEXT,
                trophies INTEGER,
                total_brawlers INTEGER,
                max_power INTEGER,
                star_powers INTEGER,
                gadgets INTEGER,
                price INTEGER,
                payment_method TEXT,
                payment_details TEXT,
                email TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)
        await db.commit()


async def get_free_email() -> tuple | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, email, password FROM emails WHERE status='free' LIMIT 1"
        ) as cursor:
            return await cursor.fetchone()


async def assign_email(email_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE emails SET status='busy', user_id=? WHERE id=?",
            (user_id, email_id)
        )
        await db.commit()


async def release_email(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE emails SET status='free', user_id=NULL WHERE user_id=?",
            (user_id,)
        )
        await db.commit()


async def get_user_email(user_id: int) -> tuple | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT email, password FROM emails WHERE user_id=?",
            (user_id,)
        ) as cursor:
            return await cursor.fetchone()


async def add_email(email: str, password: str) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO emails (email, password) VALUES (?, ?)",
                (email, password)
            )
            await db.commit()
        return True
    except Exception:
        return False


async def count_emails() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM emails WHERE status='free'") as c:
            free = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM emails WHERE status='busy'") as c:
            busy = (await c.fetchone())[0]
    return {"free": free, "busy": busy}


async def save_deal(data: dict, email: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO deals 
            (user_id, tag, name, trophies, total_brawlers, max_power, star_powers, gadgets, price, payment_method, payment_details, email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("user_id"), data.get("tag"), data.get("name"),
            data.get("trophies"), data.get("total_brawlers"), data.get("max_power"),
            data.get("star_powers"), data.get("gadgets"), data.get("price"),
            data.get("payment_method"), data.get("payment_details"), email
        ))
        await db.commit()


# ─── mail.tm API ───────────────────────────────────────────────────────────────

MAILTM_BASE = "https://api.mail.tm"

async def mailtm_login(email: str, password: str) -> str | None:
    """Получаем токен от mail.tm"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{MAILTM_BASE}/token",
                json={"address": email, "password": password},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("token")
    except Exception as e:
        print(f"mail.tm login error: {e}")
    return None


async def mailtm_get_code(email: str, password: str) -> str | None:
    """Ищем код Supercell в письмах"""
    token = await mailtm_login(email, password)
    if not token:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {token}"}
            async with session.get(
                f"{MAILTM_BASE}/messages",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None
                messages = await resp.json()

            # Ищем последнее письмо от Supercell
            hydra_members = messages.get("hydra:member", [])
            for msg in hydra_members:
                from_addr = msg.get("from", {}).get("address", "")
                if "supercell" in from_addr.lower() or "noreply" in from_addr.lower():
                    msg_id = msg.get("id")
                    # Читаем полное письмо
                    async with session.get(
                        f"{MAILTM_BASE}/messages/{msg_id}",
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as detail_resp:
                        if detail_resp.status == 200:
                            detail = await detail_resp.json()
                            text = detail.get("text", "") or detail.get("html", "")
                            # Ищем 6-значный код
                            match = re.search(r'\b(\d{6})\b', text)
                            if match:
                                return match.group(1)
    except Exception as e:
        print(f"mail.tm get_code error: {e}")
    return None


# ─── Brawlify API ──────────────────────────────────────────────────────────────

async def fetch_player(tag: str) -> dict | None:
    tag = tag.strip().lstrip("#").upper()
    url = f"https://api.brawlify.com/v1/players/%23{tag}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        print(f"Brawlify error: {e}")
    return None


def calculate_price(trophies: int) -> int:
    return int(trophies * 0.12)


# ─── States ────────────────────────────────────────────────────────────────────

class SellFlow(StatesGroup):
    choosing_game = State()
    entering_tag = State()
    confirming_price = State()
    entering_payment = State()
    waiting_transfer = State()
    waiting_code = State()
    waiting_video = State()

class AdminFlow(StatesGroup):
    adding_emails = State()


# ─── Клавиатуры ────────────────────────────────────────────────────────────────

def game_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Brawl Stars", callback_data="game_bs")],
    ])

def sell_confirm_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Продать аккаунт", callback_data="sell_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sell_cancel")],
    ])

def payment_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Карта / СБП", callback_data="pay_card")],
        [InlineKeyboardButton(text="💛 ЮMoney", callback_data="pay_yoomoney")],
        [InlineKeyboardButton(text="💎 TON", callback_data="pay_ton")],
        [InlineKeyboardButton(text="⚡️ Fragment", callback_data="pay_fragment")],
    ])

def get_code_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Получить код", callback_data="get_code")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sell_cancel")],
    ])

def transferred_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Аккаунт перепривязан", callback_data="account_transferred")],
        [InlineKeyboardButton(text="🔍 Получить код ещё раз", callback_data="get_code")],
    ])


# ─── Хендлеры — пользователь ───────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "✨ <b>Добро пожаловать в сервис скупки игровых аккаунтов!</b>\n\n"
        "🎉 Здесь твои игровые достижения превращаются в реальные деньги.\n"
        "Мы обеспечиваем быстрые выплаты и полную защиту каждой сделки.\n\n"
        "💎 Аккаунт какой игры ты хочешь продать?",
        reply_markup=game_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SellFlow.choosing_game)


@dp.callback_query(F.data == "game_bs")
async def game_selected(call: CallbackQuery, state: FSMContext):
    await state.update_data(game="Brawl Stars")
    await call.message.edit_text(
        "🎮 <b>Brawl Stars</b>\n\n"
        "🔖 Укажи свой тег игрока\n"
        "<i>Пример: #2C9CPP2900</i>",
        parse_mode="HTML"
    )
    await state.set_state(SellFlow.entering_tag)


@dp.callback_query(F.data == "sell_cancel")
async def sell_cancel(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    await release_email(user_id)
    await state.clear()
    await call.message.edit_text("❌ Отменено. Напиши /start чтобы начать заново.")


@dp.message(SellFlow.entering_tag)
async def tag_entered(message: Message, state: FSMContext):
    tag = message.text.strip().lstrip("#").upper()
    wait_msg = await message.answer("🔄 Анализируем твой аккаунт... 💰")

    player = await fetch_player(tag)
    if not player:
        await wait_msg.delete()
        await message.answer(
            "❌ Аккаунт не найден. Проверь тег и попробуй снова.\n<i>Пример: #2C9CPP2900</i>",
            parse_mode="HTML"
        )
        return

    name = player.get("name", "—")
    trophies = player.get("trophies", 0)
    highest = player.get("highestTrophies", 0)
    brawlers = player.get("brawlers", [])
    total_brawlers = len(brawlers)
    max_power = sum(1 for b in brawlers if b.get("power", 0) == 11)
    star_powers = sum(len(b.get("starPowers", [])) for b in brawlers)
    gadgets = sum(len(b.get("gadgets", [])) for b in brawlers)
    price = calculate_price(trophies)
    price_bonus = int(price * 1.1)

    await state.update_data(
        user_id=message.from_user.id,
        tag=tag, name=name, trophies=trophies,
        total_brawlers=total_brawlers, max_power=max_power,
        star_powers=star_powers, gadgets=gadgets, price=price,
    )

    await wait_msg.delete()
    await message.answer(
        f"👤 <b>{name}</b>  <code>#{tag}</code>\n\n"
        f"🏆 Кубков: <b>{trophies:,}</b>\n"
        f"🏅 Рекорд: <b>{highest:,}</b>\n"
        f"🦸 Всего бравлеров: <b>{total_brawlers}</b>\n"
        f"💪 Макс. прокачка (11): <b>{max_power}</b>\n"
        f"⭐️ Звёздных сил: <b>{star_powers}</b>\n"
        f"🔧 Гаджетов: <b>{gadgets}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Готовы выкупить за: <b>{price} ₽</b>\n"
        f"🎁 Смени ник на <code>TG:твой_ник</code> → <b>+10%</b> = <b>{price_bonus} ₽</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        reply_markup=sell_confirm_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SellFlow.confirming_price)


@dp.callback_query(F.data == "sell_confirm")
async def sell_confirmed(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "💳 <b>Выбери способ получения оплаты:</b>",
        reply_markup=payment_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SellFlow.entering_payment)


@dp.callback_query(F.data.startswith("pay_"), SellFlow.entering_payment)
async def payment_chosen(call: CallbackQuery, state: FSMContext):
    method_map = {
        "pay_card": "Карта / СБП",
        "pay_yoomoney": "ЮMoney",
        "pay_ton": "TON",
        "pay_fragment": "Fragment",
    }
    prompts = {
        "pay_card": "Введи реквизиты:\n\n<code>Банк - номер карты или телефон</code>\n\nПример:\n<code>Сбер - 4276 1234 5678 9012</code>\n<code>СБП - +79001234567 ВТБ</code>",
        "pay_yoomoney": "Введи номер кошелька ЮMoney:\n<code>41001XXXXXXXXX</code>",
        "pay_ton": "Введи TON-адрес:\n<code>UQC...xxx</code>",
        "pay_fragment": "Введи Fragment username:\n<code>@username</code>",
    }
    method = method_map.get(call.data, "Карта")
    await state.update_data(payment_method=method)
    await call.message.edit_text(
        f"💳 Способ: <b>{method}</b>\n\n" + prompts.get(call.data, "Введи реквизиты:"),
        parse_mode="HTML"
    )


@dp.message(SellFlow.entering_payment)
async def payment_entered(message: Message, state: FSMContext):
    await state.update_data(payment_details=message.text)
    user_id = message.from_user.id

    # Выдаём почту из пула
    email_row = await get_free_email()
    if not email_row:
        await message.answer(
            "⚠️ К сожалению, сейчас нет свободных слотов для обработки заявок.\n"
            "Попробуй через несколько минут."
        )
        return

    email_id, email, _ = email_row
    await assign_email(email_id, user_id)
    await state.update_data(assigned_email=email)

    await message.answer(
        "✅ <b>Реквизиты сохранены!</b>\n\n"
        "📋 <b>Инструкция по перепривязке аккаунта:</b>\n\n"
        "1️⃣ Войди в игру и перейди в раздел <b>Supercell ID</b>\n"
        "2️⃣ Нажми <b>«Сменить почту»</b>\n"
        "3️⃣ Введи код, который придёт на твою текущую почту\n"
        f"4️⃣ Введи новый адрес почты: <code>{email}</code>\n"
        "5️⃣ Нажми кнопку ниже чтобы получить код для новой почты\n\n"
        "🎁 <b>Бонус +10%:</b> смени ник в игре на <code>TG:твой_ник</code> перед выходом",
        reply_markup=get_code_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SellFlow.waiting_code)


@dp.callback_query(F.data == "get_code", SellFlow.waiting_code)
async def get_code(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    email_row = await get_user_email(call.from_user.id)

    if not email_row:
        await call.answer("❌ Почта не найдена", show_alert=True)
        return

    email, password = email_row
    wait_msg = await call.message.answer("🔍 Ищу письмо с кодом...")

    code = await mailtm_get_code(email, password)

    await wait_msg.delete()

    if code:
        await call.message.answer(
            f"✅ <b>Код найден!</b>\n\n"
            f"🔑 Твой код: <code>{code}</code>\n\n"
            f"Введи его в игре для подтверждения новой почты.\n"
            f"После успешной перепривязки нажми кнопку ниже 👇",
            reply_markup=transferred_keyboard(),
            parse_mode="HTML"
        )
    else:
        await call.message.answer(
            "🔗 Кода на почте нет. Если уверен что код отправлен — нажми кнопку ещё раз 🪃",
            reply_markup=get_code_keyboard()
        )


@dp.callback_query(F.data == "get_code", SellFlow.waiting_transfer)
async def get_code_again(call: CallbackQuery, state: FSMContext):
    await state.set_state(SellFlow.waiting_code)
    await get_code(call, state)
    await state.set_state(SellFlow.waiting_transfer)


@dp.callback_query(F.data == "account_transferred")
async def account_transferred(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "🎥 Отлично! Теперь пришли <b>видео</b>, где видно что ты вышел из аккаунта.\n\n"
        "<i>Видео нужно для подтверждения сделки.</i>",
        parse_mode="HTML"
    )
    await state.set_state(SellFlow.waiting_video)


@dp.message(SellFlow.waiting_video, F.video | F.document)
async def video_received(message: Message, state: FSMContext):
    data = await state.get_data()
    email_row = await get_user_email(message.from_user.id)
    email = email_row[0] if email_row else "—"

    await save_deal(data, email)

    await message.answer(
        "✅ <b>Видео получено!</b>\n\n"
        "🔍 Проверяем передачу аккаунта...\n"
        "💰 Деньги поступят в течение <b>15–30 минут</b>.\n\n"
        "Спасибо за доверие! 🙏",
        parse_mode="HTML"
    )

    admin_text = (
        f"🔔 <b>НОВАЯ ЗАЯВКА НА ПРОДАЖУ</b>\n\n"
        f"👤 @{message.from_user.username or '—'} (ID: <code>{message.from_user.id}</code>)\n"
        f"🎮 <b>Brawl Stars</b>\n"
        f"🏷 Тег: <code>#{data.get('tag', '—')}</code>\n"
        f"👾 Ник: <b>{data.get('name', '—')}</b>\n"
        f"🏆 Кубков: <b>{data.get('trophies', 0):,}</b>\n"
        f"🦸 Бравлеров: <b>{data.get('total_brawlers', 0)}</b>\n"
        f"💪 Макс.(11): <b>{data.get('max_power', 0)}</b>\n"
        f"⭐️ Звёздных сил: <b>{data.get('star_powers', 0)}</b>\n"
        f"🔧 Гаджетов: <b>{data.get('gadgets', 0)}</b>\n\n"
        f"📧 Почта: <code>{email}</code>\n"
        f"💵 Оценка: <b>{data.get('price', 0)} ₽</b>\n"
        f"💳 Оплата: <b>{data.get('payment_method', '—')}</b>\n"
        f"📋 Реквизиты: <code>{data.get('payment_details', '—')}</code>\n\n"
        f"📹 Видео ниже ⬇️"
    )

    try:
        await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML")
        await message.forward(ADMIN_ID)
    except Exception as e:
        print(f"Ошибка отправки админу: {e}")

    await release_email(message.from_user.id)
    await state.clear()


@dp.message(SellFlow.waiting_video)
async def no_video(message: Message):
    await message.answer("📹 Пришли именно <b>видео</b>, не фото и не текст.", parse_mode="HTML")


# ─── Админка ───────────────────────────────────────────────────────────────────

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    counts = await count_emails()
    await message.answer(
        f"👨‍💼 <b>Админ панель</b>\n\n"
        f"📧 Почт свободно: <b>{counts['free']}</b>\n"
        f"📧 Почт занято: <b>{counts['busy']}</b>\n\n"
        f"Чтобы добавить почты — отправь список в формате:\n"
        f"<code>/addemails</code>\n"
        f"<code>email1@mail.tm:пароль1</code>\n"
        f"<code>email2@mail.tm:пароль2</code>",
        parse_mode="HTML"
    )


@dp.message(Command("addemails"))
async def cmd_addemails(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    lines = message.text.strip().split("\n")[1:]  # убираем первую строку с командой
    added = 0
    errors = 0

    for line in lines:
        line = line.strip()
        if ":" in line:
            parts = line.split(":", 1)
            email, password = parts[0].strip(), parts[1].strip()
            success = await add_email(email, password)
            if success:
                added += 1
            else:
                errors += 1

    counts = await count_emails()
    await message.answer(
        f"✅ Добавлено: <b>{added}</b>\n"
        f"❌ Ошибок (дубли): <b>{errors}</b>\n\n"
        f"📧 Всего свободных почт: <b>{counts['free']}</b>",
        parse_mode="HTML"
    )


# ─── Запуск ────────────────────────────────────────────────────────────────────

async def main():
    await init_db()
    print("✅ Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
