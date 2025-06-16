# server.py - полная серверная реализация
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
import time
import hashlib
import secrets

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
DATABASE = 'pc_control.db'
API_PORT = 8080
API_SECRET = secrets.token_hex(16)  # Секретный ключ для API
ADMIN_IDS = [5276367440]  # Ваш Telegram ID

# Инициализация FastAPI
api = FastAPI()

class ServerBot:
    def __init__(self, token):
        self.token = token
        self.app = ApplicationBuilder().token(self.token).build()
        self.setup_handlers()
        self.init_db()

    def init_db(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        
        # Таблица устройств
        c.execute('''CREATE TABLE IF NOT EXISTS devices
                    (id TEXT PRIMARY KEY,
                     name TEXT,
                     ip TEXT,
                     os TEXT,
                     last_seen TIMESTAMP,
                     is_online INTEGER DEFAULT 0)''')
        
        # Таблица пользователей
        c.execute('''CREATE TABLE IF NOT EXISTS users
                    (user_id INTEGER PRIMARY KEY,
                     username TEXT)''')
        
        # Таблица команд
        c.execute('''CREATE TABLE IF NOT EXISTS commands
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     device_id TEXT,
                     command TEXT,
                     status TEXT DEFAULT 'pending',
                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY(device_id) REFERENCES devices(id))''')
        
        conn.commit()
        conn.close()

    def setup_handlers(self):
        """Настройка обработчиков Telegram бота"""
        self.app.add_handlers([
            CommandHandler("start", self.start_command),
            CommandHandler("devices", self.list_devices_command),
            CommandHandler("help", self.help_command),
            CallbackQueryHandler(self.callback_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.message_handler)
        ])

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /start"""
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("⛔ Доступ запрещен")
            return
            
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", 
                 (user_id, update.effective_user.username))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            "🖥️ Панель управления ПК\n\n"
            "Доступные команды:\n"
            "/devices - список устройств\n"
            "/help - справка"
        )

    async def list_devices_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /devices"""
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("⛔ Доступ запрещен")
            return
            
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT id, name, ip, is_online FROM devices")
        devices = c.fetchall()
        conn.close()
        
        if not devices:
            await update.message.reply_text("Нет подключенных устройств")
            return
            
        keyboard = []
        for device in devices:
            device_id, name, ip, is_online = device
            status = "🟢" if is_online else "🔴"
            btn_text = f"{status} {name} ({ip})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"device_{device_id}")])
        
        await update.message.reply_text(
            "Список устройств:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка inline кнопок"""
        query = update.callback_query
        await query.answer()
        
        if query.data.startswith("device_"):
            device_id = query.data[7:]
            await self.show_device_actions(query, device_id)
        elif query.data.startswith("action_"):
            parts = query.data[7:].split("_")
            device_id = parts[0]
            action = "_".join(parts[1:])
            await self.handle_device_action(query, device_id, action)

    async def show_device_actions(self, query, device_id):
        """Показать действия для устройства"""
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT name, is_online FROM devices WHERE id=?", (device_id,))
        device = c.fetchone()
        conn.close()
        
        if not device:
            await query.edit_message_text("Устройство не найдено")
            return
            
        name, is_online = device
        status = "онлайн" if is_online else "оффлайн"
        
        keyboard = []
        if is_online:
            actions = [
                ("🖥 Скриншот", "screenshot"),
                ("📷 Веб-камера", "webcam"),
                ("🔌 Выключить", "shutdown"),
                ("🔄 Перезагрузить", "reboot"),
                ("⌨️ Команда", "custom_cmd")
            ]
            for text, action in actions:
                keyboard.append([InlineKeyboardButton(text, callback_data=f"action_{device_id}_{action}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_devices")])
        
        await query.edit_message_text(
            f"Устройство: {name}\nСтатус: {status}\n\nВыберите действие:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def handle_device_action(self, query, device_id, action):
        """Обработка действий с устройством"""
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        
        # Добавляем команду в очередь
        c.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                 (device_id, action))
        command_id = c.lastrowid
        conn.commit()
        conn.close()
        
        await query.edit_message_text(f"Команда '{action}' отправлена на устройство. Ожидайте выполнения...")

    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        pass

    def run(self):
        """Запуск бота"""
        self.app.run_polling()

# API endpoints
@api.post("/register")
async def register_device(request: Request):
    """Регистрация нового устройства"""
    data = await request.json()
    
    # Проверка секретного ключа
    if request.headers.get("X-Auth-Token") != API_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Обновляем информацию об устройстве
    c.execute('''INSERT OR REPLACE INTO devices 
                (id, name, ip, os, last_seen, is_online)
                VALUES (?, ?, ?, ?, datetime('now'), 1)''',
              (data['device_id'], data['system_info']['name'],
               data['system_info']['ip'], data['system_info']['os']))
    
    conn.commit()
    conn.close()
    
    return JSONResponse({"status": "success"})

@api.post("/heartbeat")
async def heartbeat(request: Request):
    """Обновление статуса устройства"""
    data = await request.json()
    
    if request.headers.get("X-Auth-Token") != API_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("UPDATE devices SET last_seen=datetime('now'), is_online=1 WHERE id=?",
              (data['device_id'],))
    conn.commit()
    conn.close()
    
    return JSONResponse({"status": "success"})

@api.get("/commands")
async def get_commands(device_id: str, request: Request):
    """Получение команд для устройства"""
    if request.headers.get("X-Auth-Token") != API_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Получаем pending команды
    c.execute("SELECT id, command FROM commands WHERE device_id=? AND status='pending'",
             (device_id,))
    commands = [{"id": row[0], "command": row[1]} for row in c.fetchall()]
    
    # Помечаем команды как полученные
    if commands:
        c.execute("UPDATE commands SET status='executing' WHERE id IN ({})".format(
            ",".join(str(cmd['id']) for cmd in commands)))
        conn.commit()
    
    conn.close()
    
    return JSONResponse({"commands": commands})

@api.post("/command_result")
async def command_result(request: Request):
    """Отправка результата выполнения команды"""
    data = await request.json()
    
    if request.headers.get("X-Auth-Token") != API_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("UPDATE commands SET status=? WHERE id=?",
              ("success" if data['success'] else "failed", data['command_id']))
    conn.commit()
    conn.close()
    
    # Здесь можно добавить отправку уведомления в Telegram
    
    return JSONResponse({"status": "success"})

def run_api():
    """Запуск API сервера"""
    uvicorn.run(api, host="0.0.0.0", port=API_PORT)

if __name__ == '__main__':
    # Запускаем API в отдельном потоке
    api_thread = Thread(target=run_api, daemon=True)
    api_thread.start()
    
    # Запускаем Telegram бота
    bot = ServerBot("8060699147:AAEawF_dYzDuEA7lqF_FHCuHsujuMwF4r8k")
    
    print(f"Сервер запущен. API секрет: {API_SECRET}")
    print(f"API доступен по адресу: http://95.163.84.18:{API_PORT}")
    
    bot.run()