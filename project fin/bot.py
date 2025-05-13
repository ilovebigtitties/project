import logging
import asyncio
import random
import aiohttp
import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove
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
MAX_CITIES_IN_GAME = 200  # Лимит городов в одной игре

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
                return list(set(cities + default_cities))  # Удаляем дубликаты
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
active_games: Dict[str, Dict[str, Any]] = {}
user_stats: Dict[int, Dict[str, int]] = {}

# --- Утилиты ---
def get_last_letter(city: str) -> str:
    """Получаем последнюю букву (исключая 'ь', 'ы' и др.)"""
    bad_letters = ["ь", "ы", "й", "ъ", "ё"]
    last_char = city[-1].lower()
    return city[-2].lower() if last_char in bad_letters else last_char

async def get_wiki_info(city: str) -> str:
    """Получаем информацию о городе из Википедии"""
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
    """Генерируем фейковое описание города"""
    facts = [
        f"{city} основан в {random.randint(10, 21)} веке",
        f"Население: ~{random.randint(50, 500)} тыс. человек",
        f"Известен своим {random.choice(['университетом', 'метро', 'парком'])}",
        f"Главная достопримечательность: {random.choice(['башня', 'мост', 'музей'])}"
    ]
    return random.choice(facts)

def create_fake_city() -> str:
    """Генерируем название фейкового города"""
    prefixes = ["Ново", "Верхне", "Нижне", "Старо", "Бело"]
    suffixes = ["град", "бург", "поль", "донск", "горск"]
    return random.choice(prefixes) + random.choice(suffixes)

def is_valid_city(city: str, last_letter: str, used_cities: List[str]) -> bool:
    """Проверяем валидность города"""
    return (city in CITIES or city in FAKE_CITIES) and city not in used_cities and city[0].lower() == last_letter.lower()

# --- Клавиатуры ---
def main_menu_kb() -> ReplyKeyboardMarkup:
    """Клавиатура главного меню"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎮 Одиночная игра")
    builder.button(text="👥 Мультиплеер")
    builder.button(text="📊 Статистика")
    builder.button(text="ℹ Помощь")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def difficulty_kb() -> ReplyKeyboardMarkup:
    """Клавиатура выбора сложности"""
    builder = ReplyKeyboardBuilder()
    for diff in DIFFICULTIES.values():
        builder.button(text=diff["name"])
    builder.button(text="🔙 Назад")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def game_kb(hints: bool = False) -> ReplyKeyboardMarkup:
    """Игровая клавиатура"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🏳 Сдаться")
    if hints:
        builder.button(text="💡 Подсказка")
    builder.button(text="❓ Что за город?")
    return builder.as_markup(resize_keyboard=True)

def hint_kb(letter: str, cities: List[str]) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура с подсказками"""
    builder = InlineKeyboardBuilder()
    for city in cities[:5]:  # Показываем первые 5 вариантов
        builder.button(text=city, callback_data=f"hint_{city}")
    return builder.as_markup()

# --- Основные обработчики ---
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Обработка команды /start"""
    await state.set_state(GameState.MAIN_MENU)
    await message.answer(
        "🏙 <b>Игра в Города</b>\n\n"
        "Правила:\n"
        "1. Называйте города на последнюю букву предыдущего\n"
        "2. Нельзя повторять города\n"
        "3. В сложном режиме бот может 'мухлевать'\n\n"
        "Выберите режим игры:",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )
    logger.info(f"Пользователь {message.from_user.id} запустил бота")

@dp.message(StateFilter(GameState.MAIN_MENU), lambda m: m.text == "🎮 Одиночная игра")
async def singleplayer_mode(message: Message, state: FSMContext):
    """Выбор одиночной игры"""
    await state.set_state(GameState.CHOOSING_DIFFICULTY)
    await message.answer(
        "Выберите уровень сложности:",
        reply_markup=difficulty_kb()
    )

@dp.message(StateFilter(GameState.MAIN_MENU), lambda m: m.text == "👥 Мультиплеер")
async def multiplayer_mode(message: Message, state: FSMContext):
    """Выбор мультиплеера"""
    await state.set_state(GameState.WAITING_PLAYER)
    await message.answer(
        "👥 <b>Мультиплеер</b>\n\n"
        "Пришлите @username или ID второго игрока\n"
        "Или перешлите его сообщение",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )

@dp.message(StateFilter(GameState.MAIN_MENU), lambda m: m.text == "📊 Статистика")
async def show_stats(message: Message):
    """Показ статистики игрока"""
    stats = user_stats.get(message.from_user.id, {"wins": 0, "losses": 0})
    await message.answer(
        f"📊 <b>Ваша статистика</b>\n\n"
        f"🏆 Побед: {stats['wins']}\n"
        f"💀 Поражений: {stats['losses']}\n"
        f"🏙 Всего городов в базе: {len(CITIES)}",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )

@dp.message(StateFilter(GameState.MAIN_MENU), lambda m: m.text == "ℹ Помощь")
async def show_help(message: Message):
    """Показ помощи"""
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
    await message.answer(help_text, reply_markup=main_menu_kb(), parse_mode="HTML")

@dp.message(StateFilter(GameState.CHOOSING_DIFFICULTY))
async def set_difficulty(message: Message, state: FSMContext):
    """Установка уровня сложности для одиночной игры"""
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
    
    # Первый ход бота
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
        reply_markup=game_kb(DIFFICULTIES[diff_name]["hints"]),
        parse_mode="HTML"
    )

@dp.message(StateFilter(GameState.PLAYING_SINGLE))
async def game_process(message: Message, state: FSMContext):
    """Обработка хода в одиночной игре"""
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    
    if not session:
        await message.answer("Сессия не найдена. Начните заново /start")
        return
    
    # Обновляем время последнего хода
    session["last_move"] = datetime.now()
    session["turn_count"] += 1
    
    # Обработка специальных команд
    if message.text == "🏳 Сдаться":
        await end_single_game(user_id, "Вы сдались")
        await state.set_state(GameState.MAIN_MENU)
        return
    
    if message.text == "💡 Подсказка" and DIFFICULTIES[session["difficulty"]]["hints"]:
        last_letter = get_last_letter(session["used"][-1])
        available = [c for c in CITIES 
                    if c[0].lower() == last_letter
                    and c not in session["used"]]
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
                "⚠ Если считаете, что города нет, напишите <b>Фейк</b>",
                parse_mode="HTML"
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
                reply_markup=main_menu_kb(),
                parse_mode="HTML"
            )
            update_stats(user_id, True)
            await state.set_state(GameState.MAIN_MENU)
        else:
            await message.answer("Это реальный город! Продолжайте игру")
        return
    
    # Проверка города игрока
    city = message.text.strip().capitalize()
    last_city = session["used"][-1]
    required_letter = get_last_letter(last_city)
    
    if city in session["used"]:
        await message.answer("Этот город уже был!")
        return
        
    if city[0].lower() != required_letter:
        await message.answer(f"Нужен город на букву <b>{required_letter.upper()}</b>!", parse_mode="HTML")
        return
        
    if city not in CITIES and city not in FAKE_CITIES:
        await message.answer("Я не знаю такого города!")
        return
    
    # Ход принят
    session["used"].append(city)
    session["score"]["player"] += 1
    
    # Проверка на победу (если использованы все города)
    if len(session["used"]) >= MAX_CITIES_IN_GAME:
        await end_single_game(user_id, "Достигнут лимит городов")
        await state.set_state(GameState.MAIN_MENU)
        return
    
    # Ход бота
    last_letter = get_last_letter(city)
    available = [c for c in CITIES 
                if c not in session["used"] 
                and c[0].lower() == last_letter]
    
    # Проверка на блеф (на сложном уровне после 3 ходов)
    if (random.random() < DIFFICULTIES[session["difficulty"]]["cheat_chance"] 
            and session["turn_count"] > 3):
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
        reply_markup=game_kb(DIFFICULTIES[session["difficulty"]]["hints"]),
        parse_mode="HTML"
    )

# --- Мультиплеер ---
@dp.message(StateFilter(GameState.WAITING_PLAYER))
async def process_player2(message: Message, state: FSMContext):
    """Обработка приглашения второго игрока"""
    try:
        if message.forward_from:
            player2 = message.forward_from.id
        else:
            player2 = message.text.strip().lstrip("@")
    except Exception as e:
        logger.error(f"Ошибка обработки игрока 2: {e}")
        await message.answer("Неверный формат. Пришлите @username или перешлите сообщение")
        return
    
    # Проверка что это не сам бот
    if str(player2) == str((await bot.get_me()).id):
        await message.answer("Нельзя играть с самим собой!")
        return
    
    # Создаем игру
    game_id = str(random.randint(1000, 9999))
    active_games[game_id] = {
        "player1": message.from_user.id,
        "player2": player2,
        "used": [],
        "scores": {
            str(message.from_user.id): 0,
            str(player2): 0
        },
        "current_turn": player2,  # Первый ход у приглашенного
        "last_move": datetime.now(),
        "started": False
    }
    
    # Отправляем приглашение
    try:
        invite_text = (
            f"🎮 <b>Приглашение в игру Города</b>\n\n"
            f"Игрок @{message.from_user.username} приглашает вас!\n"
            f"ID игры: <code>{game_id}</code>\n\n"
            f"Напишите /join_{game_id} чтобы присоединиться"
        )
        
        await bot.send_message(
            player2,
            invite_text,
            parse_mode="HTML"
        )
        await message.answer(
            f"✅ Приглашение отправлено игроку @{player2}\n"
            f"Ожидаем подтверждения...\n\n"
            f"ID игры: <code>{game_id}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка отправки приглашения: {e}")
        await message.answer(
            "❌ Не удалось отправить приглашение\n"
            "Проверьте username или ID игрока"
        )
        await state.set_state(GameState.MAIN_MENU)

@dp.message(Command("join"))
async def join_game(message: Message, state: FSMContext):
    """Обработка входа в игру"""
    try:
        game_id = message.text.split()[1]
    except IndexError:
        await message.answer("Используйте: /join [ID_игры]")
        return
    
    if game_id not in active_games:
        await message.answer("Игра не найдена или уже завершена")
        return
    
    game = active_games[game_id]
    player2 = str(message.from_user.id)
    
    if player2 != str(game["player2"]):
        await message.answer("Вы не являетесь приглашенным игроком")
        return
    
    if game["started"]:
        await message.answer("Игра уже началась")
        return
    
    # Начинаем игру
    game["started"] = True
    first_city = random.choice(CITIES)
    game["used"].append(first_city)
    
    # Сохраняем сессии
    user_sessions[game["player1"]] = {"game_id": game_id}
    user_sessions[player2] = {"game_id": game_id}
    
    # Уведомляем игроков
    await bot.send_message(
        game["player1"],
        f"🎮 Игра #{game_id} началась!\n"
        f"Первый город: <b>{first_city}</b>\n"
        f"Следующий ход - у соперника",
        reply_markup=game_kb(),
        parse_mode="HTML"
    )
    
    await message.answer(
        f"🎮 Игра #{game_id} началась!\n"
        f"Первый город: <b>{first_city}</b>\n"
        f"Ваш ход! Назовите город на букву <b>{get_last_letter(first_city).upper()}</b>",
        reply_markup=game_kb(),
        parse_mode="HTML"
    )
    
    await state.set_state(GameState.PLAYING_MULTI)

@dp.message(StateFilter(GameState.PLAYING_MULTI))
async def multiplayer_turn(message: Message, state: FSMContext):
    """Обработка хода в мультиплеере"""
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    
    if not session or "game_id" not in session:
        await message.answer("Сессия не найдена. Начните заново /start")
        return
    
    game_id = session["game_id"]
    game = active_games.get(game_id)
    
    if not game:
        await message.answer("Игра не найдена")
        await state.set_state(GameState.MAIN_MENU)
        return
    
    # Проверяем, чей сейчас ход
    if str(user_id) != str(game["current_turn"]):
        await message.answer("Сейчас не ваш ход!")
        return
    
    # Обновляем таймер
    game["last_move"] = datetime.now()
    
    # Обработка спецкоманд
    if message.text == "🏳 Сдаться":
        winner_id = game["player2"] if user_id == game["player1"] else game["player1"]
        await end_multiplayer_game(game_id, winner_id, "игрок сдался")
        await state.set_state(GameState.MAIN_MENU)
        return
    
    if message.text == "❓ Что за город?":
        if not game["used"]:
            await message.answer("Еще нет названных городов")
            return
        
        last_city = game["used"][-1]
        info = await get_wiki_info(last_city)
        await message.answer(f"📖 {last_city}\n{info}")
        return
    
    # Проверка города
    city = message.text.strip().capitalize()
    last_city = game["used"][-1] if game["used"] else ""
    
    if city in game["used"]:
        await message.answer("Этот город уже был!")
        return
        
    if game["used"] and city[0].lower() != get_last_letter(last_city):
        await message.answer(
            f"Нужен город на букву <b>{get_last_letter(last_city).upper()}</b>!",
            parse_mode="HTML"
        )
        return
        
    if city not in CITIES:
        await message.answer("Я не знаю такого города!")
        return
    
    # Ход принят
    game["used"].append(city)
    game["scores"][str(user_id)] += 1
    
    # Проверка на победу (если использованы все города)
    if len(game["used"]) >= MAX_CITIES_IN_GAME:
        player1_score = game["scores"][str(game["player1"])]
        player2_score = game["scores"][str(game["player2"])]
        winner_id = game["player1"] if player1_score > player2_score else game["player2"]
        await end_multiplayer_game(game_id, winner_id, "достигнут лимит городов")
        await state.set_state(GameState.MAIN_MENU)
        return
    
    # Определяем следующего игрока
    opponent_id = game["player2"] if user_id == game["player1"] else game["player1"]
    game["current_turn"] = opponent_id
    
    # Уведомляем игроков
    await message.answer(
        f"✅ <b>{city}</b> принят!\n"
        f"Ожидаем ход соперника...",
        reply_markup=ReplyKeyboardRemove()
    )
    
    await bot.send_message(
        opponent_id,
        f"🏙 Соперник назвал: <b>{city}</b>\n"
        f"📌 Ваш ход на букву <b>{get_last_letter(city).upper()}</b>\n"
        f"📊 Счет: Вы {game['scores'][str(opponent_id)]} - {game['scores'][str(user_id)]} Соперник",
        reply_markup=game_kb(),
        parse_mode="HTML"
    )

# --- Вспомогательные функции ---
async def end_single_game(user_id: int, reason: str):
    """Завершение одиночной игры"""
    session = user_sessions.get(user_id)
    if not session:
        return
    
    player_score = session["score"]["player"]
    bot_score = session["score"]["bot"]
    
    if player_score > bot_score:
        result = "вы победили"
        update_stats(user_id, True)
    elif player_score < bot_score:
        result = "бот победил"
        update_stats(user_id, False)
    else:
        result = "ничья"
    
    await bot.send_message(
        user_id,
        f"🏁 Игра окончена! {reason}\n"
        f"📊 Счет: {player_score}-{bot_score}\n"
        f"🎉 Результат: {result}",
        reply_markup=main_menu_kb()
    )
    del user_sessions[user_id]

async def end_multiplayer_game(game_id: str, winner_id: int, reason: str):
    """Завершение мультиплеерной игры"""
    game = active_games.get(game_id)
    if not game:
        return
    
    player1 = game["player1"]
    player2 = game["player2"]
    
    result_text = (
        f"🏁 Игра завершена! {reason}\n\n"
        f"📊 Счет:\n"
        f"Игрок 1: {game['scores'][str(player1)]}\n"
        f"Игрок 2: {game['scores'][str(player2)]}\n\n"
        f"🏆 Победитель: {'Игрок 1' if winner_id == player1 else 'Игрок 2'}"
    )
    
    # Отправляем результаты
    await bot.send_message(player1, result_text, reply_markup=main_menu_kb())
    await bot.send_message(player2, result_text, reply_markup=main_menu_kb())
    
    # Обновляем статистику
    if winner_id in (player1, player2):
        update_stats(winner_id, True)
        loser_id = player2 if winner_id == player1 else player1
        update_stats(loser_id, False)
    
    # Очистка
    if str(player1) in user_sessions:
        del user_sessions[str(player1)]
    if str(player2) in user_sessions:
        del user_sessions[str(player2)]
    if game_id in active_games:
        del active_games[game_id]

def update_stats(user_id: int, is_win: bool):
    """Обновление статистики игрока"""
    stats = user_stats.get(user_id, {"wins": 0, "losses": 0})
    if is_win:
        stats["wins"] += 1
    else:
        stats["losses"] += 1
    user_stats[user_id] = stats

async def check_timeouts():
    """Проверка таймаутов в играх"""
    while True:
        await asyncio.sleep(10)
        now = datetime.now()
        
        # Проверяем одиночные игры
        for user_id, session in list(user_sessions.items()):
            if session.get("mode") != GameModes.SINGLE:
                continue
                
            time_passed = (now - session["last_move"]).seconds
            time_limit = DIFFICULTIES[session["difficulty"]]["time"]
            
            if time_passed > time_limit:
                await end_single_game(user_id, "время вышло")
        
        # Проверяем мультиплеер
        for game_id, game in list(active_games.items()):
            if not game.get("started"):
                continue
                
            time_passed = (now - game["last_move"]).seconds
            if time_passed > 120:  # 2 минуты на ход
                inactive_player = game["current_turn"]
                active_player = game["player2"] if inactive_player == game["player1"] else game["player1"]
                await end_multiplayer_game(game_id, active_player, "время вышло")

# --- Запуск ---
async def on_startup():
    """Действия при запуске"""
    asyncio.create_task(check_timeouts())
    logger.info("Бот запущен")

async def main():
    dp.startup.register(on_startup)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.critical(f"Фатальная ошибка: {e}")