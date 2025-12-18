# pip install aiogram==2.25.1 aiosqlite requests
import datetime
import asyncio
import logging
import os
import time
import requests
import aiosqlite

from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from asyncio import Lock

# ================= НАСТРОЙКИ =================
API_TOKEN = "8089023622:AAEUc8InFdHCCMw6tIjRJbqRFpIGdL0SiAY"
CRYPTO_PAY_TOKEN = "503282:AAhicdmjgL8Xdl1CuQBAuTAKfkMUY5Vs81M"

ADMINS = [7502766261, 7647339913, 7775660406, 8326123233]
ACCOUNT_PRICE = 1.5
INVOICE_TTL = 600  # 10 минут

# ============================================
logging.basicConfig(level=logging.INFO)

bot = Bot(API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())
DB_LOCK = Lock()

# ================= FSM =================
class TopUp(StatesGroup):
    waiting_amount = State()

class Buy(StatesGroup):
    choosing_amount = State()
    confirm = State()

class Broadcast(StatesGroup):
    waiting_text = State()

class AdminGive(StatesGroup):
    waiting_uid = State()
    waiting_amount = State()

class AdminHistory(StatesGroup):
    waiting_uid = State()

class AdminStates(StatesGroup):
    waiting_toggle_ban = State()

# ================= DATABASE =================
async def init_db():
    os.makedirs("cookies", exist_ok=True)
    async with aiosqlite.connect("shop.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            uid TEXT UNIQUE,
            balance REAL DEFAULT 0
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            sold INTEGER DEFAULT 0
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id INTEGER PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            paid INTEGER DEFAULT 0,
            created_at INTEGER
        )""")
        await db.commit()

import secrets

def generate_uid():
    return "U-" + secrets.token_hex(3).upper()

async def is_user_banned(user_id: int) -> bool:

    async with aiosqlite.connect("shop.db") as db:
        cur = await db.execute(
            "SELECT is_banned FROM users WHERE user_id=?",
            (user_id,)
        )
        row = await cur.fetchone()
        return bool(row and row[0])


async def set_ban(value: int, uid: str = None, tg_id: int = None):
    async with aiosqlite.connect("shop.db") as db:
        if uid:
            await db.execute(
                "UPDATE users SET is_banned=? WHERE uid=?",
                (value, uid)
            )
        elif tg_id:
            await db.execute(
                "UPDATE users SET is_banned=? WHERE user_id=?",
                (value, tg_id)
            )
        await db.commit()


async def get_balance(user_id):
    async with aiosqlite.connect("shop.db") as db:

        cur = await db.execute(
            "SELECT balance, uid FROM users WHERE user_id=?",
            (user_id,)
        )
        row = await cur.fetchone()

        if not row:
            uid = generate_uid()
            await db.execute(
                "INSERT INTO users (user_id, balance, uid) VALUES (?, 0, ?)",
                (user_id, uid)
            )
            await db.commit()
            return 0

        return row[0]

async def change_balance(user_id: int, amount: float):
    async with aiosqlite.connect("shop.db") as db:
        # если пользователя нет — создаём
        cur = await db.execute(
            "SELECT balance FROM users WHERE user_id=?",
            (user_id,)
        )
        row = await cur.fetchone()

        if not row:
            uid = generate_uid()
            await db.execute(
                "INSERT INTO users (user_id, uid, balance) VALUES (?, ?, ?)",
                (user_id, uid, amount)
            )
        else:
            await db.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id=?",
                (amount, user_id)
            )

        await db.commit()


# ================= CRYPTOPAY =================
def create_invoice(amount, user_id):
    r = requests.post(
        "https://pay.crypt.bot/api/createInvoice",
        headers={"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN},
        json={"asset": "USDT", "amount": amount, "payload": str(user_id)}
    ).json()
    return r["result"]

# ================= KEYBOARDS =================

def amount_kb(max_count: int = 5):
    kb = InlineKeyboardMarkup(row_width=3)

    for i in range(1, max_count + 1):
        kb.insert(
            InlineKeyboardButton(
                text=str(i),
                callback_data=f"buy_amount:{i}"
            )
        )

    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back"))
    return kb

def main_kb(is_admin=False):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🎁 Купить аккаунт", callback_data="buy"))
    kb.add(InlineKeyboardButton("💎 Пополнить баланс", callback_data="topup"))
    if is_admin:
        kb.add(InlineKeyboardButton("🎅 Админка", callback_data="admin"))
    return kb

back_kb = InlineKeyboardMarkup().add(
    InlineKeyboardButton("⬅️ Назад", callback_data="back")
)

async def catalog_kb():
    async with aiosqlite.connect("shop.db") as db:
        cur = await db.execute("SELECT COUNT(*) FROM accounts WHERE sold=0")
        count = (await cur.fetchone())[0]

    kb = InlineKeyboardMarkup(row_width=1)

    if count == 0:
        kb.add(
            InlineKeyboardButton(
                f"🎄 MARKTPLAATS🇳🇱 Саморег без тени — нет в наличии",
                callback_data="no_items"
            )
        )
    else:
        kb.add(
            InlineKeyboardButton(
                f"🎄 MARKTPLAATS🇳🇱 Саморег без тени — {count} шт — {ACCOUNT_PRICE} USDT",
                callback_data="buy_mp"
            )
        )

    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back"))
    return kb



@dp.callback_query_handler(lambda c: c.data == "no_items")
async def no_items(call: types.CallbackQuery):
    await call.answer("❌ Товар временно отсутствует", show_alert=True)



admin_kb = InlineKeyboardMarkup(row_width=1)
admin_kb.add(InlineKeyboardButton("➕ Добавить куки", callback_data="add"))
admin_kb.add(InlineKeyboardButton("📢 Оповещение", callback_data="broadcast"))
admin_kb.add(InlineKeyboardButton("🎁 Выдать баланс", callback_data="give"))
admin_kb.add(InlineKeyboardButton("📊 История по UID", callback_data="admin_uid_history"))
admin_kb.add(InlineKeyboardButton("🚫 Бан / Разбан пользователя", callback_data="admin_toggle_ban"))
admin_kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back"))


async def safe_delete(msg: types.Message):
    try:
        await msg.delete()
    except:
        pass


# ================= MENU =================
from aiogram.types import InputFile

async def send_menu(chat_id: int, user_id: int):
    async with aiosqlite.connect("shop.db") as db:
        cur = await db.execute(
            "SELECT banned FROM users WHERE user_id=?",
            (user_id,)
        )
        row = await cur.fetchone()
        if row and row[0]:
            await bot.send_message(
                chat_id,
                "🚫 Вы заблокированы. Обратитесь в поддержку."
            )
            return

    async def send_menu(chat_id: int, user_id: int):

        # 🔒 ПРОВЕРКА БАНА
        if await is_user_banned(user_id):
            await bot.send_message(
                chat_id,
                "🚫 Вы заблокированы.\nОбратитесь в поддержку."
            )
            return

    bal = await get_balance(user_id)

    async with aiosqlite.connect("shop.db") as db:
        cur = await db.execute(
            "SELECT uid FROM users WHERE user_id=?",
            (user_id,)
        )
        uid = (await cur.fetchone())[0]


    text = (
        "🎄 Главное меню\n"
        "✨ Выберите действие ✨\n"
        f"🆔 ID: {uid}\n"
        f"💰 Баланс: {bal} USDT"
    )

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🛒 Купить аккаунт", callback_data="buy"))
    kb.add(InlineKeyboardButton("💰 Пополнить баланс", callback_data="topup"))
    kb.add(InlineKeyboardButton("📖 FAQ", callback_data="faq"))
    kb.add(InlineKeyboardButton("#BURGER-SQUAD", url="https://t.me/+bv7LVSzd1CUxYjQy"))

    if user_id in ADMINS:
        kb.add(InlineKeyboardButton("⚙️ Админка", callback_data="admin"))

    await bot.send_photo(
        chat_id=chat_id,
        photo=InputFile("burger.jpg"),
        caption=text,
        reply_markup=kb
    )


@dp.callback_query_handler(lambda c: c.data == "menu", state="*")
async def menu_cb(call: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await safe_delete(call.message)
    await send_menu(
        chat_id=call.message.chat.id,
        user_id=call.from_user.id
    )



@dp.callback_query_handler(lambda c: c.data == "back", state="*")
async def back(call: types.CallbackQuery, state: FSMContext):
    await state.finish()   # ← ВАЖНО
    await safe_delete(call.message)
    await send_menu(
        chat_id=call.message.chat.id,
        user_id=call.from_user.id
    )





@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    await msg.delete()
    await send_menu(
        chat_id=msg.chat.id,
        user_id=msg.from_user.id
    )

@dp.callback_query_handler(lambda c: c.data == "faq")
async def faq(call: types.CallbackQuery):
    await safe_delete(call.message)

    text = (
        "📖 FAQ\n\n"
        "ℹ️ Информация о магазине\n\n🛍 Мы продаем цифровые товары\n💰 Оплата через криптовалюту\n📦 Моментальная выдача товаров\n\n💬 Поддержка: @neo_ebet"






    
        "\n\n❗┃ Условия участия в проекте\n\nУважаемые участники, настоятельно рекомендуем внимательно ознакомиться с положениями, определяющими правила использования проекта.\n\n1. Виртуальный счёт\n\n• Все средства, отображаемые в боте, носят исключительно виртуальный характер и не являются реальными денежными активами.\n\n• Администрация вправе в любой момент скорректировать или обнулить виртуальный баланс без предварительного уведомления пользователя.\n\n2. Отсутствие возвратов\n\n• Любые операции, совершённые с применением виртуальной валюты, считаются завершёнными и не подлежат пересмотру.\n\n• Возврат виртуальных средств, включая отмену чеков и операций, не осуществляется.\n\n3. Корректное взаимодействие\n\n• Недопустимы оскорбления, грубость или неуважительное общение в адрес службы поддержки либо администрации проекта.\n\n• В случае нарушения данного пункта администрация оставляет за собой право ограничить доступ пользователя без дополнительного объяснения.\n\n4. Право отказа в обслуживании\n\n• Администрация может отказать в предоставлении услуг по своему усмотрению без указания причин. Несмотря на стремление к высокому качеству сервиса, данное право сохраняется за проектом.\n\n5. Обмен товаров (обязательное видеоподтверждение)\n\n• Рассмотрение запроса на замену товара возможно только при наличии непрерывной видеозаписи.\n\n• Обратите внимание: видео должно начинаться до момента покупки и фиксировать весь процесс до окончания проверки товара.\n\n• Записи, сделанные после завершения покупки, к рассмотрению не принимаются.\n\n• Срок гарантированной проверки аккаунта — 10 минут."
    )

    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("⬅️ Назад", callback_data="back")
    )

    await call.message.answer(text, reply_markup=kb)



# ================= TOPUP =================
@dp.callback_query_handler(lambda c: c.data == "topup")
async def topup(call: types.CallbackQuery):

    # 🔒 ПРОВЕРКА БАНА
    if await is_user_banned(call.from_user.id):
        await call.answer("🚫 Вы заблокированы", show_alert=True)
        return

    await safe_delete(call.message)

    await call.message.answer("💎 Введите сумму пополнения (USDT):", reply_markup=back_kb)
    await TopUp.waiting_amount.set()

@dp.message_handler(state=TopUp.waiting_amount)
async def topup_amount(msg: types.Message, state: FSMContext):
    try:
        amount = float(msg.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await msg.answer("❌ Введите корректную сумму, например: 1.5")
        return

    invoice = create_invoice(amount, msg.from_user.id)


    async with aiosqlite.connect("shop.db") as db:
        await db.execute(
            "INSERT INTO invoices VALUES (?,?,?,?,?)",
            (invoice["invoice_id"], msg.from_user.id, amount, 0, int(time.time()))
        )
        await db.commit()

    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🎄 Проверить оплату", callback_data=f"check_{invoice['invoice_id']}")
    )

    await msg.answer(f"🎁 Оплатите {amount} USDT:\n{invoice['pay_url']}", reply_markup=kb)
    await state.finish()

# ================= CHECK PAYMENT =================
@dp.callback_query_handler(lambda c: c.data.startswith("check_"))
async def check_payment(call: types.CallbackQuery):

    # 🔒 ПРОВЕРКА БАНА
    if await is_user_banned(call.from_user.id):
        await call.answer("🚫 Вы заблокированы", show_alert=True)
        return



    invoice_id = int(call.data.split("_")[1])

    r = requests.post(
        "https://pay.crypt.bot/api/getInvoices",
        headers={"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN},
        json={"invoice_ids": [invoice_id]}
    ).json()

    items = r.get("result", {}).get("items", [])
    if not items:
        await call.answer("❌ Счёт не найден", show_alert=True)
        return

    inv = items[0]

    async with aiosqlite.connect("shop.db") as db:
        cur = await db.execute(
            "SELECT paid, created_at, user_id FROM invoices WHERE invoice_id=?",
            (invoice_id,)
        )
        row = await cur.fetchone()
        if not row:
            return

        paid, created, user_id = row

        if paid:
            await call.answer("✅ Уже зачислено")
            return

        if time.time() - created > INVOICE_TTL:
            try:
                await call.message.edit_reply_markup(
                    InlineKeyboardMarkup().add(
                        InlineKeyboardButton("⌛ Счёт истёк", callback_data="noop")
                    )
                )
            except:
                pass
            return

        if inv["status"] != "paid":
            await call.answer("⏳ Оплата ещё не поступила")
            return

        await db.execute(
            "UPDATE invoices SET paid=1 WHERE invoice_id=?",
            (invoice_id,)
        )
        await db.commit()

    await change_balance(user_id, float(inv["amount"]))

    async with aiosqlite.connect("shop.db") as db:
        cur = await db.execute("SELECT uid FROM users WHERE user_id=?", (user_id,))
        uid = (await cur.fetchone())[0]

    os.makedirs("logs", exist_ok=True)
    with open("logs/topups.log", "a", encoding="utf-8") as log:
        log.write(
            f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"uid={uid} | "
            f"tg_id={user_id} | "
            f"+{inv['amount']} USDT\n"
        )

    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("📋 Меню", callback_data="back")
    )

    await call.message.delete()
    await call.message.answer(
        f"🎉 Баланс успешно пополнен!\n💰 +{inv['amount']} USDT",
        reply_markup=kb
    )



# ================= CATALOG & BUY =================



@dp.callback_query_handler(lambda c: c.data == "buy")
async def show_catalog(call: types.CallbackQuery):
    await safe_delete(call.message)
    await call.message.answer(
        "🎄 Каталог товаров 🎄",
        reply_markup=await catalog_kb()
    )


# ===== ШАГ 1: НАЧАЛО ПОКУПКИ (выбор количества) =====
@dp.callback_query_handler(lambda c: c.data == "buy_mp")
async def start_buy(call: types.CallbackQuery, state: FSMContext):

    # 🔒 ПРОВЕРКА БАНА
    if await is_user_banned(call.from_user.id):
        await call.answer("🚫 Вы заблокированы", show_alert=True)
        return

    await safe_delete(call.message)

    async with aiosqlite.connect("shop.db") as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM accounts WHERE sold=0"
        )
        available = (await cur.fetchone())[0]

    if available == 0:
        await call.answer("❌ Товар закончился", show_alert=True)
        return

    await state.update_data(max_available=available)

    kb = InlineKeyboardMarkup(row_width=3)
    for i in range(1, min(available, 5) + 1):
        kb.insert(
            InlineKeyboardButton(
                text=str(i),
                callback_data=f"buy_amount:{i}"
            )
        )
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back"))

    await call.message.answer(
        "📦 Выберите количество аккаунтов:",
        reply_markup=kb
    )

    await Buy.choosing_amount.set()


# ===== ШАГ 2: ПОДТВЕРЖДЕНИЕ =====
@dp.callback_query_handler(
    lambda c: c.data.startswith("buy_amount"),
    state=Buy.choosing_amount
)
async def choose_amount(call: types.CallbackQuery, state: FSMContext):
    amount = int(call.data.split(":")[1])
    total_price = amount * ACCOUNT_PRICE

    await state.update_data(amount=amount)

    await safe_delete(call.message)

    text = (
        "🧾 Подтверждение покупки\n\n"
        f"Товар: MARKTPLAATS 🇳🇱\n"
        f"Количество: {amount}\n"
        f"Цена за 1: {ACCOUNT_PRICE} USDT\n"
        f"💰 Итого: {total_price} USDT"
    )

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Подтвердить", callback_data="buy_confirm"))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="buy_back"))

    await call.message.answer(text, reply_markup=kb)
    await Buy.confirm.set()


# ===== ШАГ 3: ПОКУПКА =====
@dp.callback_query_handler(lambda c: c.data == "buy_confirm", state=Buy.confirm)
async def confirm_buy(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    total_price = amount * ACCOUNT_PRICE
    user_id = call.from_user.id

    balance = await get_balance(user_id)
    if balance < total_price:
        await call.answer("❌ Недостаточно средств", show_alert=True)
        return

    async with aiosqlite.connect("shop.db") as db:
        cur = await db.execute(
            "SELECT id, filename FROM accounts WHERE sold=0 LIMIT ?",
            (amount,)
        )
        accounts = await cur.fetchall()

        if len(accounts) < amount:
            await call.answer("❌ Недостаточно товара", show_alert=True)
            return

        for acc_id, _ in accounts:
            await db.execute(
                "UPDATE accounts SET sold=1 WHERE id=?",
                (acc_id,)
            )

        await db.commit()

    # отправка файлов
    for _, filename in accounts:
        path = f"cookies/{filename}"
        if os.path.exists(path):
            with open(path, "rb") as f:
                await bot.send_document(user_id, f)

            os.remove(path)

    await change_balance(user_id, -total_price)
    filenames = [f for _, f in accounts]

    # лог
    os.makedirs("logs", exist_ok=True)
    with open("logs/sales.log", "a", encoding="utf-8") as log:
        async with aiosqlite.connect("shop.db") as db:
            cur = await db.execute("SELECT uid FROM users WHERE user_id=?", (user_id,))
            uid = (await cur.fetchone())[0]

        log.write(
            f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"uid={uid} | "
            f"tg_id={user_id} | "
            f"file={filename} | "
            f"price={ACCOUNT_PRICE}\n"
        )

    await state.finish()
    await safe_delete(call.message)

    new_balance = await get_balance(user_id)

    await call.message.answer(
        f"🎉 Покупка успешна!\n💰 Остаток баланса: {new_balance} USDT"
    )

    await send_menu(
        chat_id=call.message.chat.id,
        user_id=user_id
    )


# ===== НАЗАД ИЗ ПОДТВЕРЖДЕНИЯ =====
@dp.callback_query_handler(lambda c: c.data == "buy_back", state=Buy.confirm)
async def back_to_amount(call: types.CallbackQuery, state: FSMContext):
    await safe_delete(call.message)

    data = await state.get_data()
    available = data.get("max_available", 5)

    kb = InlineKeyboardMarkup(row_width=3)
    for i in range(1, min(available, 5) + 1):
        kb.insert(
            InlineKeyboardButton(
                text=str(i),
                callback_data=f"buy_amount:{i}"
            )
        )
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back"))

    await call.message.answer(
        "📦 Выберите количество аккаунтов:",
        reply_markup=kb
    )

    await Buy.choosing_amount.set()

# ================= ADMIN =================



@dp.callback_query_handler(lambda c: c.data == "admin_toggle_ban")
async def admin_toggle_ban_start(call: types.CallbackQuery):
    if call.from_user.id not in ADMINS:
        return

    await call.message.answer(
        "🚫 Введите UID или TG ID пользователя\n"
        "Бот сам определит — бан или разбан"
    )

    await AdminStates.waiting_toggle_ban.set()

@dp.message_handler(state=AdminStates.waiting_toggle_ban)
async def admin_toggle_ban(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in ADMINS:
        await state.finish()
        return

    value = msg.text.strip()

    async with aiosqlite.connect("shop.db") as db:
        if value.isdigit():
            cur = await db.execute(
                "SELECT banned FROM users WHERE user_id=? OR uid=?",
                (int(value), value)
            )
        else:
            cur = await db.execute(
                "SELECT banned FROM users WHERE uid=?",
                (value,)
            )

        row = await cur.fetchone()

        if not row:
            await msg.answer("❌ Пользователь не найден")
            await state.finish()
            return

        banned = row[0]

        new_status = 0 if banned else 1

        if value.isdigit():
            await db.execute(
                "UPDATE users SET banned=? WHERE user_id=? OR uid=?",
                (new_status, int(value), value)
            )
        else:
            await db.execute(
                "UPDATE users SET banned=? WHERE uid=?",
                (new_status, value)
            )

        await db.commit()

    if new_status:
        await msg.answer("🚫 Пользователь ЗАБАНЕН")
    else:
        await msg.answer("✅ Пользователь РАЗБАНЕН")

    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "admin_uid_history")
async def admin_uid_history_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMINS:
        return

    await safe_delete(call.message)

    await call.message.answer(
        "🆔 Введите UID пользователя для просмотра истории:",
        reply_markup=back_kb
    )

    await AdminHistory.waiting_uid.set()


@dp.message_handler(state=AdminHistory.waiting_uid)
async def admin_uid_history_show(msg: types.Message, state: FSMContext):
    uid = msg.text.strip().upper()

    topups = []
    sales = []

    # ---------- ПОПОЛНЕНИЯ ----------
    topup_log = "logs/topups.log"
    if os.path.exists(topup_log):
        with open(topup_log, "r", encoding="utf-8") as f:
            for line in f:
                if f"uid={uid}" in line:
                    topups.append(line.strip())

    # ---------- ПОКУПКИ ----------
    sales_log = "logs/sales.log"
    if os.path.exists(sales_log):
        with open(sales_log, "r", encoding="utf-8") as f:
            for line in f:
                if f"uid={uid}" in line:
                    sales.append(line.strip())

    await state.finish()

    if not topups and not sales:
        await msg.answer(
            f"❌ История для {uid} не найдена",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️ В админку", callback_data="admin")
            )
        )
        return

    text = f"📊 История пользователя\n🆔 UID: {uid}\n\n"

    if topups:
        text += "📥 Пополнения:\n"
        text += "\n".join(topups[-10:]) + "\n\n"
    else:
        text += "📥 Пополнения: нет данных\n\n"

    if sales:
        text += "🛒 Покупки:\n"
        text += "\n".join(sales[-10:]) + "\n"
    else:
        text += "🛒 Покупки: нет данных\n"

    await msg.answer(
        text,
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("⬅️ В админку", callback_data="admin")
        )
    )



@dp.callback_query_handler(lambda c: c.data == "admin")
async def admin(call: types.CallbackQuery):
    await safe_delete(call.message)

    if call.from_user.id in ADMINS:
        await call.message.answer("🎅 Админ-панель", reply_markup=admin_kb)

@dp.callback_query_handler(lambda c: c.data == "add")
async def add(call: types.CallbackQuery):
    await safe_delete(call.message)

    await call.message.answer("🎄 Отправьте cookie-файлы", reply_markup=back_kb)

@dp.message_handler(content_types=types.ContentType.DOCUMENT)
async def save_cookie(msg: types.Message):
    if msg.from_user.id not in ADMINS:
        return
    file = await bot.get_file(msg.document.file_id)
    await bot.download_file(file.file_path, f"cookies/{msg.document.file_name}")
    async with aiosqlite.connect("shop.db") as db:
        await db.execute("INSERT INTO accounts (filename) VALUES (?)", (msg.document.file_name,))
        await db.commit()
    await msg.answer("🎄 Cookies добавлены")

@dp.callback_query_handler(lambda c: c.data == "give")
async def give_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMINS:
        return

    await safe_delete(call.message)

    await call.message.answer(
        "🆔 Введите UID пользователя (например: U-AB12CD):",
        reply_markup=back_kb
    )

    await AdminGive.waiting_uid.set()

@dp.message_handler(state=AdminGive.waiting_uid)
async def admin_give_uid(msg: types.Message, state: FSMContext):
    uid = msg.text.strip().upper()

    async with aiosqlite.connect("shop.db") as db:
        cur = await db.execute(
            "SELECT user_id FROM users WHERE uid=?",
            (uid,)
        )
        row = await cur.fetchone()

    if not row:
        await msg.answer("❌ Пользователь с таким UID не найден")
        return

    await state.update_data(target_user_id=row[0], uid=uid)

    await msg.answer(
        "💰 Введите сумму (например: 10 или -5):",
        reply_markup=back_kb
    )

    await AdminGive.waiting_amount.set()

@dp.message_handler(state=AdminGive.waiting_amount)
async def admin_give_amount(msg: types.Message, state: FSMContext):
    try:
        amount = float(msg.text.replace(",", "."))
    except ValueError:
        await msg.answer("❄️ Введите число, например 5 или -3")
        return

    data = await state.get_data()
    user_id = data["target_user_id"]
    uid = data["uid"]

    await change_balance(user_id, amount)

    # лог
    os.makedirs("logs", exist_ok=True)
    with open("logs/admin_balance.log", "a", encoding="utf-8") as log:
        log.write(
            f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"admin={msg.from_user.id} | "
            f"uid={uid} | "
            f"change={amount}\n"
        )

    await state.finish()

    await msg.answer(
        f"✅ Баланс пользователя {uid} изменён на {amount} USDT",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("📋 В меню", callback_data="back")
        )
    )

@dp.callback_query_handler(lambda c: c.data == "broadcast")
async def broadcast_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMINS:
        return

    await safe_delete(call.message)

    await call.message.answer(
        "📢 Введите текст оповещения для всех пользователей:",
        reply_markup=back_kb
    )

    await Broadcast.waiting_text.set()

@dp.message_handler(state=Broadcast.waiting_text)
async def broadcast_send(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in ADMINS:
        return

    text = msg.text
    sent = 0
    failed = 0

    async with aiosqlite.connect("shop.db") as db:
        cur = await db.execute("SELECT user_id FROM users")
        users = await cur.fetchall()

    for (user_id,) in users:
        try:
            await bot.send_message(
                user_id,
                f"  \n\n{text}"
            )
            sent += 1
            await asyncio.sleep(0.05)  # анти-флуд
        except:
            failed += 1

    await msg.answer(
        f"✅ Оповещение отправлено\n"
        f"📨 Успешно: {sent}\n"
        f"❌ Ошибки: {failed}"
    )

    await state.finish()



# ================= START =================
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    executor.start_polling(dp, skip_updates=True)
