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
            tests_done INTEGER DEFAULT 0
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
    markup.add("📊 Прогресс", "📈 График", "⚙️ Настройки")
    return markup

# Управление данными пользователя
def get_user_data(user_id):
    conn = sqlite3.connect('study_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    conn.close()
    return {
        "videos": {"done": data[1], "total": 128},
        "practices": {"done": data[2], "total": 50, "check_days": 2},
        "tests": {"done": data[3], "total": 2}
    } if data else None

def update_user_data(user_id, field, value):
    conn = sqlite3.connect('study_bot.db')
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()
    log_progress(user_id)

def log_progress(user_id):
    data = get_user_data(user_id)
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
    end_date = datetime(2024, 9, 30)  # Укажи свою дату
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
        "tests_left": tests_left
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
    
    progress_text = f"""
📊 *Твой прогресс*:  
🎥 *Видео*: `{user_data["videos"]["done"]}/{user_data["videos"]["total"]}` (осталось {data["videos_left"]})  
📝 *Практика*: `{user_data["practices"]["done"]}/{user_data["practices"]["total"]}` (осталось {data["practices_left"]})  
📋 *Тесты*: `{user_data["tests"]["done"]}/{user_data["tests"]["total"]}` (осталось {data["tests_left"]})  

⏳ *Дней до дедлайна*: `{data["days_left"]}`  
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

# Напоминания
def send_daily_reminder():
    conn = sqlite3.connect('study_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    
    for user in users:
        data = calculate_time(user[0])
        msg = f"⏰ *Напоминание*: сегодня нужно уделить учебе ~{data['daily_min']:.1f} минут!\n_Подтверди выполнение:_"
        bot.send_message(user[0], msg, 
                         parse_mode="Markdown", 
                         reply_markup=create_confirmation_keyboard())

@bot.callback_query_handler(func=lambda call: call.data == "confirm")
def handle_confirmation(call):
    bot.answer_callback_query(call.id, "Молодец! Продолжай в том же духе 💪")
    bot.edit_message_text("✅ Задание подтверждено!", call.message.chat.id, call.message.message_id)

# Запуск планировщика
scheduler.add_job(send_daily_reminder, 'cron', hour=10, minute=0)
scheduler.start()

bot.polling()