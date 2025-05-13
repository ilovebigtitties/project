import logging
import asyncio
import random
import aiohttp
import json
import os
from datetime import datetime
from typing import Dict, Any, List

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# Настройка логов
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = "7477794349:AAGQ6A1R9VY-M1HbpoxISKNyqjyt6xiKMYw"
CITIES_FILE = "cities.txt"
FAKE_CITIES = ["Квантоград", "Нейросбург", "Киберполис", "Алгоритмск", "Датоград"]
MAX_CITIES_IN_GAME = 200

class GameModes:
    SINGLE = "single"
    MULTI = "multi"

# Загрузка городов
def load_cities() -> List[str]:
    default_cities = [
        "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
        "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону",
        "Уфа", "Красноярск", "Пермь", "Воронеж", "Волгоград"
    ]
    if os.path.exists(CITIES_FILE):
        try:
            with open(CITIES_FILE, encoding="utf-8") as f:
                cities = [line.strip() for line in f if line.strip()]
                return list(set(cities + default_cities))
        except Exception as e:
            logger.error(f"Ошибка загрузки городов: {e}")
            return default_cities
    return default_cities

CITIES = load_cities()

# Уровни сложности
DIFFICULTIES = {
    "easy": {
        "name": "👶 Легкий",
        "time": 60,
        "hints": True,
        "cheat_chance": 0,
        "description": "Подсказки доступны, бот не мухлюет"
    },
    "medium": {
        "name": "💪 Средний",
        "time": 45,
        "hints": False,
        "cheat_chance": 0,
        "description": "Без подсказок, стандартные правила"
    },
    "hard": {
        "name": "🔥 Сложный",
        "time": 30,
        "hints": False,
        "cheat_chance": 0.05,
        "description": "Бот может подсунуть фейковый город (5% шанс)"
    }
}

# Состояния игры
class GameState(StatesGroup):
    MAIN_MENU = State()
    CHOOSING_MODE = State()
    CHOOSING_DIFFICULTY = State()
    WAITING_PLAYER = State()
    PLAYING_SINGLE = State()
    PLAYING_MULTI = State()

# Инициализация бота
storage = MemoryStorage()
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher(storage=storage)

# Хранилища данных
user_sessions: Dict[int, Dict[str, Any]] = {}
user_stats: Dict[int, Dict[str, int]] = {}

# --- Утилиты ---
def get_last_letter(city: str) -> str:
    bad_letters = ["ь", "ы", "й", "ъ", "ё"]
    last_char = city[-1].lower()
    return city[-2].lower() if last_char in bad_letters else last_char

async def get_wiki_info(city: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://ru.wikipedia.org/api/rest_v1/page/summary/{city}",
                timeout=3
            ) as resp:
                data = await resp.json()
                return data.get("extract", "Информация не найдена")
    except Exception as e:
        logger.error(f"Ошибка Wikipedia: {e}")
        return "Не удалось получить информацию"

def generate_fake_info(city: str) -> str:
    facts = [
        f"{city} основан в {random.randint(10, 21)} веке",
        f"Население: ~{random.randint(50, 500)} тыс. человек",
        f"Известен своим {random.choice(['университетом', 'метро', 'парком'])}",
        f"Главная достопримечательность: {random.choice(['башня', 'мост', 'музей'])}"
    ]
    return random.choice(facts)

def create_fake_city() -> str:
    prefixes = ["Ново", "Верхне", "Нижне", "Старо", "Бело"]
    suffixes = ["град", "бург", "поль", "донск", "горск"]
    return random.choice(prefixes) + random.choice(suffixes)

def update_stats(user_id: int, win: bool):
    stats = user_stats.setdefault(user_id, {"wins": 0, "losses": 0})
    if win:
        stats["wins"] += 1
    else:
        stats["losses"] += 1

# --- Клавиатуры ---
def main_menu_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎮 Одиночная игра")
    builder.button(text="👥 Мультиплеер")
    builder.button(text="📊 Статистика")
    builder.button(text="ℹ Помощь")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def difficulty_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for diff in DIFFICULTIES.values():
        builder.button(text=diff["name"])
    builder.button(text="🔙 Назад")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def game_kb(hints: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="🏳 Сдаться")
    if hints:
        builder.button(text="💡 Подсказка")
    builder.button(text="❓ Что за город?")
    return builder.as_markup(resize_keyboard=True)

def hint_kb(letter: str, cities: List[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for city in cities[:5]:
        builder.button(text=city, callback_data=f"hint_{city}")
    return builder.as_markup()

# --- Основные обработчики ---
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.set_state(GameState.MAIN_MENU)
    await message.answer(
        "🏙 <b>Игра в Города</b>\n\n"
        "Правила:\n"
        "1. Называйте города на последнюю букву предыдущего\n"
        "2. Нельзя повторять города\n"
        "3. В сложном режиме бот может 'мухлевать'\n\n"
        "Выберите режим игры:",
        reply_markup=main_menu_kb()
    )
    logger.info(f"Пользователь {message.from_user.id} запустил бота")

@dp.message(StateFilter(GameState.MAIN_MENU), lambda m: m.text == "🎮 Одиночная игра")
async def singleplayer_mode(message: Message, state: FSMContext):
    await state.set_state(GameState.CHOOSING_DIFFICULTY)
    await message.answer(
        "Выберите уровень сложности:",
        reply_markup=difficulty_kb()
    )

@dp.message(StateFilter(GameState.MAIN_MENU), lambda m: m.text == "👥 Мультиплеер")
async def multiplayer_mode(message: Message, state: FSMContext):
    await state.set_state(GameState.WAITING_PLAYER)
    await message.answer(
        "👥 <b>Мультиплеер</b>\n\n"
        "Пришлите @username или ID второго игрока\n"
        "Или перешлите его сообщение",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(StateFilter(GameState.MAIN_MENU), lambda m: m.text == "📊 Статистика")
async def show_stats(message: Message):
    stats = user_stats.get(message.from_user.id, {"wins": 0, "losses": 0})
    await message.answer(
        f"📊 <b>Ваша статистика</b>\n\n"
        f"🏆 Побед: {stats['wins']}\n"
        f"💀 Поражений: {stats['losses']}\n"
        f"🏙 Всего городов в базе: {len(CITIES)}",
        reply_markup=main_menu_kb()
    )

@dp.message(StateFilter(GameState.MAIN_MENU), lambda m: m.text == "ℹ Помощь")
async def show_help(message: Message):
    help_text = (
        "🆘 <b>Помощь</b>\n\n"
        "Правила игры:\n"
        "1. Называйте города на последнюю букву предыдущего\n"
        "2. Нельзя повторять города\n"
        "3. В сложном режиме бот может 'мухлевать'\n\n"
        "<b>Режимы игры:</b>\n"
        "🎮 Одиночная - игра против бота\n"
        "👥 Мультиплеер - игра с другом\n\n"
        "<b>Команды:</b>\n"
        "/start - Перезапустить бота\n"
        "🏳 Сдаться - Завершить игру\n"
        "❓ Что за город? - Информация о городе\n"
        "💡 Подсказка - Доступна на легком уровне"
    )
    await message.answer(help_text, reply_markup=main_menu_kb())

@dp.message(StateFilter(GameState.CHOOSING_DIFFICULTY))
async def set_difficulty(message: Message, state: FSMContext):
    diff_name = next(
        (k for k, v in DIFFICULTIES.items() if v["name"] == message.text),
        None
    )
    if message.text == "🔙 Назад":
        await state.set_state(GameState.MAIN_MENU)
        await message.answer("Главное меню:", reply_markup=main_menu_kb())
        return
    if not diff_name:
        await message.answer("Пожалуйста, выберите сложность из списка")
        return
    user_id = message.from_user.id
    user_sessions[user_id] = {
        "mode": GameModes.SINGLE,
        "difficulty": diff_name,
        "used": [],
        "score": {"player": 0, "bot": 0},
        "last_move": datetime.now(),
        "cheated": False,
        "turn_count": 0
    }
    city = random.choice(CITIES)
    user_sessions[user_id]["used"].append(city)
    user_sessions[user_id]["score"]["bot"] += 1
    await state.set_state(GameState.PLAYING_SINGLE)
    await message.answer(
        f"🚀 Игра началась! Уровень: <b>{DIFFICULTIES[diff_name]['name']}</b>\n"
        f"{DIFFICULTIES[diff_name]['description']}\n\n"
        f"🏙 Мой город: <b>{city}</b>\n"
        f"📌 Вам на букву: <b>{get_last_letter(city).upper()}</b>\n"
        f"⏳ У вас {DIFFICULTIES[diff_name]['time']} секунд на ход",
        reply_markup=game_kb(DIFFICULTIES[diff_name]["hints"])
    )

@dp.message(StateFilter(GameState.PLAYING_SINGLE))
async def game_process(message: Message, state: FSMContext):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await message.answer("Сессия не найдена. Начните заново /start")
        return
    session["last_move"] = datetime.now()
    session["turn_count"] += 1
    if message.text == "🏳 Сдаться":
        await end_single_game(user_id, "Вы сдались")
        await state.set_state(GameState.MAIN_MENU)
        return
    if message.text == "💡 Подсказка" and DIFFICULTIES[session["difficulty"]]["hints"]:
        last_letter = get_last_letter(session["used"][-1])
        available = [c for c in CITIES if c[0].lower() == last_letter and c not in session["used"]]
        if available:
            await message.answer(
                "Возможные города:",
                reply_markup=hint_kb(last_letter, available)
            )
        else:
            await message.answer("Нет доступных подсказок")
        return
    if message.text == "❓ Что за город?":
        if not session["used"]:
            await message.answer("Еще нет названных городов")
            return
        last_city = session["used"][-1]
        if session["cheated"] and last_city in FAKE_CITIES:
            info = generate_fake_info(last_city)
            await message.answer(
                f"📖 {last_city}\n{info}\n\n"
                "⚠ Если считаете, что города нет, напишите <b>Фейк</b>"
            )
        else:
            info = await get_wiki_info(last_city)
            await message.answer(f"📖 {last_city}\n{info}")
        return
    if message.text.lower() in ["фейк", "обман"]:
        if session.get("cheated", False):
            await message.answer(
                "🎉 Вы поймали бота на обмане! Победа за вами!\n"
                f"Фейковый город: <b>{session['used'][-1]}</b>",
                reply_markup=main_menu_kb()
            )
            update_stats(user_id, True)
            await state.set_state(GameState.MAIN_MENU)
        else:
            await message.answer("Это реальный город! Продолжайте игру")
        return
    city = message.text.strip().capitalize()
    last_city = session["used"][-1]
    required_letter = get_last_letter(last_city)
    if city in session["used"]:
        await message.answer("Этот город уже был!")
        return
    if city[0].lower() != required_letter:
        await message.answer(f"Нужен город на букву <b>{required_letter.upper()}</b>!")
        return
    if city not in CITIES and city not in FAKE_CITIES:
        await message.answer("Я не знаю такого города!")
        return
    session["used"].append(city)
    session["score"]["player"] += 1
    if len(session["used"]) >= MAX_CITIES_IN_GAME:
        await end_single_game(user_id, "Достигнут лимит городов")
        await state.set_state(GameState.MAIN_MENU)
        return
    last_letter = get_last_letter(city)
    available = [c for c in CITIES if c not in session["used"] and c[0].lower() == last_letter]
    if (random.random() < DIFFICULTIES[session["difficulty"]]["cheat_chance"] and session["turn_count"] > 3):
        fake_city = create_fake_city()
        available.append(fake_city)
        session["cheated"] = True
    if not available:
        await message.answer(
            "🎉 Вы победили! У меня нет городов на эту букву.\n"
            f"📊 Счет: {session['score']['player']}-{session['score']['bot']}",
            reply_markup=main_menu_kb()
        )
        update_stats(user_id, True)
        await state.set_state(GameState.MAIN_MENU)
        return
    bot_city = random.choice(available)
    session["used"].append(bot_city)
    session["score"]["bot"] += 1
    await message.answer(
        f"✅ Принято: <b>{city}</b>\n"
        f"🤖 Мой город: <b>{bot_city}</b>\n"
        f"📌 Вам на букву: <b>{get_last_letter(bot_city).upper()}</b>\n\n"
        f"📊 Счет: Вы {session['score']['player']} - {session['score']['bot']} Бот",
        reply_markup=game_kb(DIFFICULTIES[session["difficulty"]]["hints"])
    )

async def end_single_game(user_id: int, reason: str):
    session = user_sessions.get(user_id)
    if not session:
        return
    await bot.send_message(
        user_id,
        f"🏁 Игра окончена: {reason}\n"
        f"📊 Итоговый счет: {session['score']['player']} - {session['score']['bot']}",
        reply_markup=main_menu_kb()
    )
    update_stats(user_id, False)
    user_sessions.pop(user_id, None)

# --- Fallback handler (универсальный обработчик) ---
@dp.message()
async def fallback_handler(message: Message, state: FSMContext):
    current_state = await state.get_state()
    await message.answer(
        f"Не понимаю это сообщение в текущем режиме.\n"
        f"Ваше состояние: <code>{current_state}</code>\n"
        f"Попробуйте /start для возврата в главное меню.",
        parse_mode="HTML"
    )
    logger.info(
        f"Unhandled message from {message.from_user.id} "
        f"in state={current_state}: {message.text}"
    )

# --- Запуск бота ---
async def main():
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
