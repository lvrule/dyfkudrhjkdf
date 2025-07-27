import telebot
from telebot import types
from datetime import datetime, timedelta
import sqlite3
import matplotlib.pyplot as plt
from apscheduler.schedulers.background import BackgroundScheduler
import os

TOKEN = "8387920808:AAHbBDxyOA2dJUYulaQUyPZRmY1sxtv0zes"
bot = telebot.TeleBot(TOKEN)
scheduler = BackgroundScheduler()

# Инициализация БД
def init_db():
    conn = sqlite3.connect('study_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            videos_done INTEGER DEFAULT 0,
            practices_done INTEGER DEFAULT 0,
            tests_done INTEGER DEFAULT 0,
            videos_total INTEGER DEFAULT 128,
            practices_total INTEGER DEFAULT 50,
            tests_total INTEGER DEFAULT 2,
            practice_check_days INTEGER DEFAULT 2,
            end_date TEXT DEFAULT '2025-10-30'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS progress_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            videos_done INTEGER,
            practices_done INTEGER,
            tests_done INTEGER,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Клавиатуры
def create_confirmation_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"))
    return markup

def create_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("📊 Прогресс", "📈 График", "⚙️ Настройки", "➕ Добавить выполненное")
    return markup

def create_settings_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🎥 Видео", callback_data="set_videos"),
        types.InlineKeyboardButton("📝 Практика", callback_data="set_practices"),
        types.InlineKeyboardButton("📋 Тесты", callback_data="set_tests")
    )
    markup.add(
        types.InlineKeyboardButton("📅 Дата окончания", callback_data="set_end_date"),
        types.InlineKeyboardButton("⏱ Дни проверки", callback_data="set_check_days")
    )
    return markup

def create_add_done_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🎥 Видео", callback_data="add_video"),
        types.InlineKeyboardButton("📝 Практика", callback_data="add_practice"),
        types.InlineKeyboardButton("📋 Тест", callback_data="add_test")
    )
    return markup

# Управление данными пользователя
def get_user_data(user_id):
    conn = sqlite3.connect('study_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    conn.close()
    if data:
        return {
            "videos": {"done": data[1], "total": data[4]},
            "practices": {"done": data[2], "total": data[5], "check_days": data[7]},
            "tests": {"done": data[3], "total": data[6]}
        }
    return None

def update_user_data(user_id, field, value):
    conn = sqlite3.connect('study_bot.db')
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()
    log_progress(user_id)

def update_user_setting(user_id, setting, value):
    conn = sqlite3.connect('study_bot.db')
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {setting} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()

def log_progress(user_id):
    data = get_user_data(user_id)
    if not data:
        return
        
    conn = sqlite3.connect('study_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO progress_history (user_id, date, videos_done, practices_done, tests_done)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, datetime.now().strftime("%Y-%m-%d"), data["videos"]["done"], data["practices"]["done"], data["tests"]["done"]))
    conn.commit()
    conn.close()

# Расчет времени
def calculate_time(user_id):
    data = get_user_data(user_id)
    if not data:
        return None
        
    conn = sqlite3.connect('study_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT end_date FROM users WHERE user_id = ?", (user_id,))
    end_date_str = cursor.fetchone()[0]
    conn.close()
    
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else datetime(2025, 10, 30)
    today = datetime.now()
    days_left = (end_date - today).days

    videos_left = data["videos"]["total"] - data["videos"]["done"]
    practices_left = data["practices"]["total"] - data["practices"]["done"]
    tests_left = data["tests"]["total"] - data["tests"]["done"]

    video_time = videos_left * 5
    practice_time = practices_left * 25
    test_time = tests_left * 10
    total_time_min = video_time + practice_time + test_time

    check_days = practices_left * data["practices"]["check_days"]
    effective_days = max(1, days_left - check_days)
    daily_min = total_time_min / effective_days

    return {
        "days_left": days_left,
        "effective_days": effective_days,
        "daily_min": daily_min,
        "total_time_hours": total_time_min / 60,
        "videos_left": videos_left,
        "practices_left": practices_left,
        "tests_left": tests_left,
        "end_date": end_date.strftime("%d.%m.%Y")
    }

# Генерация графика
def generate_progress_chart(user_id):
    conn = sqlite3.connect('study_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT date, videos_done, practices_done, tests_done 
        FROM progress_history 
        WHERE user_id = ? 
        ORDER BY date
    ''', (user_id,))
    data = cursor.fetchall()
    conn.close()

    if not data:
        return None

    dates = [row[0] for row in data]
    videos = [row[1] for row in data]
    practices = [row[2] for row in data]
    tests = [row[3] for row in data]

    plt.figure(figsize=(10, 5))
    plt.plot(dates, videos, label='Видео', marker='o')
    plt.plot(dates, practices, label='Практика', marker='s')
    plt.plot(dates, tests, label='Тесты', marker='^')
    plt.xlabel('Дата')
    plt.ylabel('Выполнено')
    plt.title('Прогресс обучения')
    plt.legend()
    plt.grid()
    plt.xticks(rotation=45)
    plt.tight_layout()
    
    filename = f"chart_{user_id}.png"
    plt.savefig(filename)
    plt.close()
    return filename

# Функция для напоминаний
def send_daily_reminder():
    conn = sqlite3.connect('study_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    
    for user in users:
        data = calculate_time(user[0])
        if data:
            msg = f"⏰ *Напоминание*: сегодня нужно уделить учебе ~{data['daily_min']:.1f} минут!\n_Подтверди выполнение:_"
            bot.send_message(user[0], msg, 
                           parse_mode="Markdown", 
                           reply_markup=create_confirmation_keyboard())

# Обработчики команд
@bot.message_handler(commands=['start'])
def start(message):
    conn = sqlite3.connect('study_bot.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.chat.id,))
    conn.commit()
    conn.close()
    
    bot.send_message(message.chat.id, 
                    "📚 Привет! Я твой помощник в обучении.\n"
                    "Используй кнопки ниже для управления:",
                    reply_markup=create_main_keyboard())

@bot.message_handler(func=lambda m: m.text == "📊 Прогресс")
def progress(message):
    data = calculate_time(message.chat.id)
    user_data = get_user_data(message.chat.id)
    
    if not data or not user_data:
        bot.send_message(message.chat.id, "❌ Ошибка получения данных")
        return
    
    progress_text = f"""
📊 *Твой прогресс*:  
🎥 *Видео*: `{user_data["videos"]["done"]}/{user_data["videos"]["total"]}` (осталось {data["videos_left"]})  
📝 *Практика*: `{user_data["practices"]["done"]}/{user_data["practices"]["total"]}` (осталось {data["practices_left"]})  
📋 *Тесты*: `{user_data["tests"]["done"]}/{user_data["tests"]["total"]}` (осталось {data["tests_left"]})  

⏳ *Дней до дедлайна* ({data["end_date"]}): `{data["days_left"]}`  
🔍 *Учет проверки*: `-{data["days_left"] - data["effective_days"]} дней`  
⏱ *Общее время*: `~{data["total_time_hours"]:.1f} ч`  
📅 *Ежедневно*: `~{data["daily_min"]:.1f} мин/день`  

💡 *Совет*: {'Ты впереди графика! 🎉' if data["daily_min"] < 30 else 'Стоит ускориться! 🔥'}
    """
    bot.send_message(message.chat.id, progress_text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📈 График")
def send_chart(message):
    filename = generate_progress_chart(message.chat.id)
    if filename:
        with open(filename, 'rb') as chart:
            bot.send_photo(message.chat.id, chart)
        os.remove(filename)
    else:
        bot.send_message(message.chat.id, "❌ Данных для графика пока нет!")

@bot.message_handler(func=lambda m: m.text == "⚙️ Настройки")
def settings(message):
    bot.send_message(message.chat.id, "⚙️ Настройки:", reply_markup=create_settings_keyboard())

@bot.message_handler(func=lambda m: m.text == "➕ Добавить выполненное")
def add_done(message):
    bot.send_message(message.chat.id, "Что вы выполнили?", reply_markup=create_add_done_keyboard())

# Обработчики callback
@bot.callback_query_handler(func=lambda call: call.data.startswith('set_'))
def handle_settings(call):
    user_id = call.message.chat.id
    setting = call.data[4:]
    
    if setting == "videos":
        msg = bot.send_message(user_id, "Введите общее количество видео:")
        bot.register_next_step_handler(msg, lambda m: process_setting(m, 'videos_total'))
    elif setting == "practices":
        msg = bot.send_message(user_id, "Введите общее количество практик:")
        bot.register_next_step_handler(msg, lambda m: process_setting(m, 'practices_total'))
    elif setting == "tests":
        msg = bot.send_message(user_id, "Введите общее количество тестов:")
        bot.register_next_step_handler(msg, lambda m: process_setting(m, 'tests_total'))
    elif setting == "end_date":
        msg = bot.send_message(user_id, "Введите дату окончания в формате ДД.ММ.ГГГГ:")
        bot.register_next_step_handler(msg, process_end_date)
    elif setting == "check_days":
        msg = bot.send_message(user_id, "Введите количество дней на проверку одной практики:")
        bot.register_next_step_handler(msg, lambda m: process_setting(m, 'practice_check_days'))

def process_setting(message, setting):
    try:
        value = int(message.text)
        update_user_setting(message.chat.id, setting, value)
        bot.send_message(message.chat.id, f"✅ Настройка {setting} обновлена!")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Ошибка! Введите число.")

def process_end_date(message):
    try:
        date = datetime.strptime(message.text, "%d.%m.%Y")
        conn = sqlite3.connect('study_bot.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET end_date = ? WHERE user_id = ?", (date.strftime("%Y-%m-%d"), message.chat.id))
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, "✅ Дата окончания обновлена!")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Ошибка формата! Используйте ДД.ММ.ГГГГ")

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_'))
def handle_add_done(call):
    user_id = call.message.chat.id
    action = call.data[4:]
    
    if action == "video":
        msg = bot.send_message(user_id, "Сколько видео вы посмотрели?")
        bot.register_next_step_handler(msg, lambda m: process_add(m, 'videos_done'))
    elif action == "practice":
        msg = bot.send_message(user_id, "Сколько практик вы выполнили?")
        bot.register_next_step_handler(msg, lambda m: process_add(m, 'practices_done'))
    elif action == "test":
        msg = bot.send_message(user_id, "Сколько тестов вы прошли?")
        bot.register_next_step_handler(msg, lambda m: process_add(m, 'tests_done'))

def process_add(message, field):
    try:
        value = int(message.text)
        current = get_user_data(message.chat.id)
        if current:
            new_value = current[field.split('_')[0]]["done"] + value
            update_user_data(message.chat.id, field, new_value)
            bot.send_message(message.chat.id, f"✅ Добавлено! Теперь {field.split('_')[0]}: {new_value}")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Ошибка! Введите число.")

@bot.callback_query_handler(func=lambda call: call.data == "confirm")
def handle_confirmation(call):
    bot.answer_callback_query(call.id, "Молодец! Продолжай в том же духе 💪")
    bot.edit_message_text("✅ Задание подтверждено!", call.message.chat.id, call.message.message_id)

# Запуск планировщика
scheduler.add_job(send_daily_reminder, 'cron', hour=10, minute=0)
scheduler.start()

bot.polling()