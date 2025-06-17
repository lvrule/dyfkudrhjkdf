# server.py - исправленная версия с поддержкой скриншотов и расширенным функционалом
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)
import sqlite3
import logging
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from threading import Thread
import secrets
import socket
import requests
import base64
import os
from datetime import datetime

bot_instance = None

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
DATABASE = 'pc_control.db'
API_PORT = 4443
ADMIN_IDS = [5276367440]  # Замените на ваш Telegram ID

# Инициализация FastAPI
api = FastAPI()

def is_port_in_use(port):
    """Проверка занятости порта"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

class ServerBot:
    def __init__(self, token):
        self.token = token
        self.application = ApplicationBuilder().token(self.token).build()
        self.setup_handlers()
        self.init_db()

    def init_db(self):
        """Инициализация базы данных"""
        with sqlite3.connect(DATABASE) as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS devices
                        (device_id TEXT PRIMARY KEY,
                         name TEXT,
                         ip TEXT,
                         os TEXT,
                         last_seen TIMESTAMP,
                         is_online INTEGER DEFAULT 0)''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS users
                        (user_id INTEGER PRIMARY KEY,
                         username TEXT)''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS commands
                        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                         device_id TEXT,
                         command TEXT,
                         status TEXT DEFAULT 'pending',
                         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    def setup_handlers(self):
        """Настройка обработчиков команд"""
        self.application.add_handlers([
            CommandHandler("start", self.start_command),
            CommandHandler("devices", self.list_devices_command),
            CommandHandler("refresh", self.refresh_devices_command),
            CallbackQueryHandler(self.callback_handler)
        ])

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /start"""
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("⛔ Доступ запрещен")
            return
            
        with sqlite3.connect(DATABASE) as conn:
            conn.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", 
                       (user_id, update.effective_user.username))
        
        await update.message.reply_text(
            "🖥️ Панель управления ПК\n\n"
            "Доступные команды:\n"
            "/devices - список устройств\n"
            "/refresh - обновить статус устройств"
        )

    async def refresh_devices_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /refresh - обновление статуса устройств"""
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("⛔ Доступ запрещен")
            return
            
        with sqlite3.connect(DATABASE) as conn:
            conn.execute("UPDATE devices SET is_online=0")
            c = conn.cursor()
            c.execute("SELECT device_id FROM devices")
            devices = c.fetchall()
        
        if not devices:
            await update.message.reply_text("Нет подключенных устройств")
            return
            
        await update.message.reply_text("🔄 Обновление статуса устройств...")
        await self.list_devices_command(update, context)

    async def list_devices_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /devices"""
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("⛔ Доступ запрещен")
            return
            
        with sqlite3.connect(DATABASE) as conn:
            c = conn.cursor()
            c.execute("SELECT device_id, name, ip, is_online FROM devices")
            devices = c.fetchall()
        
        if not devices:
            await update.message.reply_text("Нет подключенных устройств")
            return
            
        keyboard = [
            [InlineKeyboardButton(
                f"{'🟢' if is_online else '🔴'} {name} ({ip})",
                callback_data=f"device_{device_id}"
            )]
            for device_id, name, ip, is_online in devices
        ]
        
        # Добавляем кнопку обновления
        keyboard.append([InlineKeyboardButton("🔄 Обновить статус", callback_data="refresh_devices")])
        
        await update.message.reply_text(
            "Список устройств:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
            
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка callback-запросов"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "refresh_devices":
            await self.refresh_devices_command(query.message, context)
        elif query.data.startswith("device_"):
            await self.handle_device_action(query, query.data[7:])
        elif query.data.startswith("action_"):
            parts = query.data.split("_")
            device_id = parts[1]
            action = parts[2]
            
            if action == "screenshot":
                await self.handle_screenshot_command(query, device_id)
            elif action == "shutdown":
                await self.handle_shutdown_command(query, device_id)
            elif action == "reboot":
                await self.handle_reboot_command(query, device_id)
            elif action == "lock":
                await self.handle_lock_command(query, device_id)
            elif action == "sleep":
                await self.handle_sleep_command(query, device_id)
            elif action == "cmd":
                await self.handle_cmd_command(query, device_id)
            elif action == "processes":
                await self.handle_processes_command(query, device_id)
            elif action == "killprocess":
                await self.handle_kill_process_command(query, device_id)
        elif query.data == "back_to_devices":
            await self.list_devices_command(query.message, context)

    async def handle_device_action(self, query, device_id):
        """Обработка действий с устройством"""
        with sqlite3.connect(DATABASE) as conn:
            c = conn.cursor()
            c.execute("SELECT name, is_online FROM devices WHERE device_id=?", (device_id,))
            device = c.fetchone()
        
        if not device:
            await query.edit_message_text("Устройство не найдено")
            return
            
        name, is_online = device
        
        if is_online:
            keyboard = [
                [InlineKeyboardButton("🖥 Скриншот", callback_data=f"action_{device_id}_screenshot")],
                [InlineKeyboardButton("📜 Список процессов", callback_data=f"action_{device_id}_processes")],
                [InlineKeyboardButton("❌ Завершить процесс", callback_data=f"action_{device_id}_killprocess")],
                [InlineKeyboardButton("⌨️ Выполнить команду", callback_data=f"action_{device_id}_cmd")],
                [InlineKeyboardButton("🔒 Заблокировать", callback_data=f"action_{device_id}_lock")],
                [InlineKeyboardButton("💤 Спящий режим", callback_data=f"action_{device_id}_sleep")],
                [InlineKeyboardButton("🔌 Выключить", callback_data=f"action_{device_id}_shutdown")],
                [InlineKeyboardButton("🔄 Перезагрузить", callback_data=f"action_{device_id}_reboot")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_devices")]
            ]
            await query.edit_message_text(
                f"Управление устройством: {name}\nСтатус: онлайн\n\nВыберите действие:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.edit_message_text(
                f"Устройство {name} в настоящее время недоступно",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_devices")]]
                ))

    async def handle_screenshot_command(self, query, device_id):
        """Обработка команды скриншота"""
        with sqlite3.connect(DATABASE) as conn:
            conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                       (device_id, "screenshot"))
        
        await query.edit_message_text(
            "Команда на создание скриншота отправлена устройству. Ожидайте...",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
            ])
        )

    async def handle_shutdown_command(self, query, device_id):
        """Обработка команды выключения"""
        with sqlite3.connect(DATABASE) as conn:
            conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                       (device_id, "shutdown"))
        
        await query.edit_message_text(
            "Команда на выключение отправлена устройству",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
            ])
        )

    async def handle_reboot_command(self, query, device_id):
        """Обработка команды перезагрузки"""
        with sqlite3.connect(DATABASE) as conn:
            conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                       (device_id, "reboot"))
        
        await query.edit_message_text(
            "Команда на перезагрузку отправлена устройству",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
            ])
        )

    async def handle_lock_command(self, query, device_id):
        """Обработка команды блокировки"""
        with sqlite3.connect(DATABASE) as conn:
            conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                       (device_id, "lock"))
        
        await query.edit_message_text(
            "Команда на блокировку компьютера отправлена",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
            ])
        )

    async def handle_sleep_command(self, query, device_id):
        """Обработка команды перехода в спящий режим"""
        with sqlite3.connect(DATABASE) as conn:
            conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                       (device_id, "sleep"))
        
        await query.edit_message_text(
            "Команда на переход в спящий режим отправлена",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
            ])
        )

    async def handle_cmd_command(self, query, device_id):
        """Обработка команды выполнения CMD"""
        await query.edit_message_text(
            "Введите команду для выполнения на удаленном компьютере:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
            ])
        )
        # Здесь нужно добавить логику ожидания ввода команды от пользователя

    async def handle_processes_command(self, query, device_id):
        """Обработка команды получения списка процессов"""
        with sqlite3.connect(DATABASE) as conn:
            conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                       (device_id, "processes"))
        
        await query.edit_message_text(
            "Запрос списка процессов отправлен. Ожидайте...",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
            ])
        )

    async def handle_kill_process_command(self, query, device_id):
        """Обработка команды завершения процесса"""
        await query.edit_message_text(
            "Введите ID процесса для завершения:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
            ])
        )
        # Здесь нужно добавить логику ожидания ввода ID процесса

    def run(self):
        """Запуск бота"""
        self.application.run_polling()

# API Endpoints
@api.post("/register")
async def register_device(request: Request):
    """Регистрация нового устройства"""
    data = await request.json()
    
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('''INSERT OR REPLACE INTO devices 
                      (device_id, name, ip, os, last_seen, is_online)
                      VALUES (?, ?, ?, ?, datetime('now'), 1)''',
                   (data['device_id'], data['system_info']['name'],
                    data['system_info']['ip'], data['system_info']['os']))
    
    return {"status": "success"}

@api.post("/heartbeat")
async def heartbeat(request: Request):
    """Обновление статуса устройства"""
    data = await request.json()
    
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("UPDATE devices SET last_seen=datetime('now'), is_online=1 WHERE device_id=?",
                   (data['device_id'],))
    
    return {"status": "success"}

@api.get("/commands")
async def get_commands(device_id: str, request: Request):
    """Получение команд для устройства"""
    with sqlite3.connect(DATABASE) as conn:
        c = conn.cursor()
        c.execute("SELECT id, command FROM commands WHERE device_id=? AND status='pending'",
                 (device_id,))
        commands = [{"id": row[0], "command": row[1]} for row in c.fetchall()]
        
        if commands:
            c.execute("UPDATE commands SET status='executing' WHERE id IN ({})".format(
                ",".join(str(cmd['id']) for cmd in commands)))
    
    return {"commands": commands}

@api.post("/command_result")
async def command_result(request: Request):
    """Получение результатов выполнения команды"""
    data = await request.json()
    device_id = data['device_id']
    command_id = data['command_id']
    result = data['result']
    
    # Отправляем результат администратору
    for admin_id in ADMIN_IDS:
        try:
            await bot_instance.application.bot.send_message(
                chat_id=admin_id,
                text=f"Результат выполнения команды:\n\n{result}"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки результата администратору {admin_id}: {e}")
    
    # Обновляем статус команды в БД
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("UPDATE commands SET status='completed' WHERE id=?",
                   (command_id,))
    
    return {"status": "success"}

@api.post("/upload_screenshot")
async def upload_screenshot(request: Request):
    """Получение скриншота от клиента"""
    data = await request.json()
    
    # Декодируем изображение
    image_data = base64.b64decode(data['image'])
    
    # Сохраняем в файл
    filename = f"screenshots/{data['device_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    os.makedirs("screenshots", exist_ok=True)
    
    with open(filename, "wb") as f:
        f.write(image_data)
    
    # Отправляем изображение администратору
    for admin_id in ADMIN_IDS:
        try:
            await bot_instance.application.bot.send_photo(
                chat_id=admin_id,
                photo=image_data,
                caption=f"Скриншот с устройства {data['device_id']}"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки скриншота администратору {admin_id}: {e}")
    
    return {"status": "success"}

def run_api_server():
    """Запуск API сервера"""
    if is_port_in_use(API_PORT):
        logger.error(f"Порт {API_PORT} уже занят!")
        return
    
    config = uvicorn.Config(
        api,
        host="0.0.0.0",
        port=API_PORT,
        log_level="info"
    )
    server = uvicorn.Server(config)
    server.run()

def main():
    """Основная функция запуска"""
    # Получаем внешний IP
    try:
        external_ip = requests.get('https://api.ipify.org').text
        print(f"Ваш внешний IP: {external_ip}")
    except:
        print("Не удалось определить внешний IP")
    
    if is_port_in_use(API_PORT):
        print(f"Ошибка: Порт {API_PORT} уже используется!")
        return
    
    # Запускаем API сервер
    api_thread = Thread(target=run_api_server, daemon=True)
    api_thread.start()
    
    # Запускаем бота
    try:
        bot = ServerBot("8060699147:AAEawF_dYzDuEA7lqF_FHCuHsujuMwF4r8k")
        global bot_instance
        bot_instance = bot
        print(f"\nСервер запущен:")
        print(f"Локальный адрес: http://localhost:{API_PORT}")
        print(f"Внешний адрес: http://{external_ip}:{API_PORT}")
        bot.run()
    except Exception as e:
        print(f"Ошибка при запуске бота: {e}")

if __name__ == '__main__':
    main()