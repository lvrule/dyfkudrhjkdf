import telebot
from datetime import datetime, timedelta

TOKEN = "8387920808:AAHbBDxyOA2dJUYulaQUyPZRmY1sxtv0zes"
bot = telebot.TeleBot(TOKEN)

# Твои данные
total_videos = 128
total_practices = 50
total_tests = 2

current_videos = 17
current_practices = 2
current_tests = 0

# Цель — закончить к сентябрю (например, 30 сентября)
end_date = datetime(2025, 9, 30)  # Укажи свою дату
today = datetime.now()
days_left = (end_date - today).days

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "Привет! Я твой помощник в обучении. Напиши /progress, чтобы увидеть прогресс.")

@bot.message_handler(commands=['progress'])
def progress(message):
    videos_left = total_videos - current_videos
    practices_left = total_practices - current_practices
    tests_left = total_tests - current_tests

    # Расчет времени
    video_time = videos_left * 5  # минут
    practice_time = practices_left * 25
    test_time = tests_left * 10
    total_time_min = video_time + practice_time + test_time
    total_time_hours = total_time_min / 60

    # Рекомендации
    daily_min = total_time_min / days_left if days_left > 0 else 0

    progress_text = f"""
📊 **Твой прогресс:**  
🎥 Видео: {current_videos}/{total_videos} (осталось {videos_left})  
📝 Практика: {current_practices}/{total_practices} (осталось {practices_left})  
📋 Тесты: {current_tests}/{total_tests} (осталось {tests_left})  

⏳ **Дней до дедлайна:** {days_left}  
⏱ **Общее время на всё:** ~{total_time_hours:.1f} ч  
📅 **Ежедневно:** ~{daily_min:.1f} мин/день  

💡 Совет: {'Ты впереди графика! 🎉' if daily_min < 30 else 'Пора взяться за учебу! 🔥'}
    """
    bot.reply_to(message, progress_text)

@bot.message_handler(commands=['remind'])
def set_reminder(message):
    bot.reply_to(message, "Напоминание установлено! Я буду писать тебе каждый день в 10:00.")

# Запуск бота
bot.polling()