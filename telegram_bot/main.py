import asyncio
import json
import os
import re
import subprocess
from typing import Optional, Dict, List, Tuple, Set
from telethon import TelegramClient, events
from telethon.tl.types import Message
import aiohttp
import ast
import time
import signal

# Константы
API_ID = 20548178
API_HASH = '833bdf2bd79bf249fab75c16421f10f7'
SESSION_NAME = 'session_name'
CONFIG_FILE = 'config.json'
DEFAULT_PREFIX = '!'
MAX_DELETE = 100
MAX_SPAM = 500  # Максимальное количество сообщений для спама
MAX_DELAY = 60  # Максимальная задержка в секундах
PREFIX_MAX_LENGTH = 5
FORBIDDEN_CHARS = r'^$*+?.[]{}()|/\''
AUTOANSWER_API_URL = "https://api.intelligence.io.solutions/api/v1/chat/completions"
AUTOANSWER_API_KEY = "Bearer io-v2-eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJvd25lciI6IjNiYzhkNjZiLWFkNzctNDJhOS04ZTY4LTkzNzdiOTE2NzM1NSIsImV4cCI6NDkwNjIxOTU1Nn0.JNyKBZgZ7R1McSaDNXPhRQTb2A90SLHoI9n3g6JDJXQRcV4-l1TF6mgQj0-cndcelq2Nbow_vy3Gp5BYAe-7RQ"
MESSAGE_GROUP_DELAY = 2.0  # Максимальная задержка между сообщениями для объединения в группу


class Config:
    def __init__(self):
        self.prefix = DEFAULT_PREFIX
        self.flood_mode = False
        self.autoanswer_users: Dict[int, bool] = {}
        self.gift_notify_chats: Set[int] = set()  # Чаты, где нужно уведомлять о новых подарках
        self.known_gifts: Set[str] = set()  # Известные ID подарков

    def load(self):
        """Загружает конфигурацию из файла"""
        if not os.path.exists(CONFIG_FILE):
            return
            
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                self.prefix = data.get('prefix', DEFAULT_PREFIX)
                self.flood_mode = data.get('flood_mode', False)
                self.autoanswer_users = data.get('autoanswer_users', {})
                self.gift_notify_chats = set(data.get('gift_notify_chats', []))
                self.known_gifts = set(data.get('known_gifts', []))
        except (json.JSONDecodeError, IOError) as e:
            print(f"Ошибка загрузки конфига: {e}")

    def save(self):
        """Сохраняет конфигурацию в файл"""
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump({
                    'prefix': self.prefix,
                    'flood_mode': self.flood_mode,
                    'autoanswer_users': self.autoanswer_users,
                    'gift_notify_chats': list(self.gift_notify_chats),
                    'known_gifts': list(self.known_gifts)
                }, f)
        except IOError as e:
            print(f"Ошибка сохранения конфига: {e}")


class MyTelegramClient(TelegramClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = Config()
        self.active_spam_tasks: Dict[int, asyncio.Task] = {}
        self.http_session = aiohttp.ClientSession()
        self.message_buffer: Dict[Tuple[int, int], List[Tuple[float, str]]] = {}
        self.processing_users: Dict[Tuple[int, int], asyncio.Task] = {}
        self.generation_tasks: Dict[int, asyncio.Task] = {}
        self.gift_check_task: Optional[asyncio.Task] = None  # Задача проверки подарков

    async def disconnect(self):
        """Закрываем HTTP-сессию и отменяем задачи при отключении"""
        if self.gift_check_task:
            self.gift_check_task.cancel()
        for task in self.generation_tasks.values():
            task.cancel()
        await self.http_session.close()
        await super().disconnect()

    async def get_available_gifts(self) -> Optional[dict]:
        """Получает список доступных подарков"""
        url = "https://api.telegram.org/bot8387920808:AAHbBDxyOA2dJUYulaQUyPZRmY1sxtv0zes/getAvailableGifts"
        try:
            async with self.http_session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('ok'):
                        return data.get('result', {}).get('gifts', [])
        except Exception as e:
            print(f"Ошибка при получении подарков: {e}")
        return None

    async def check_new_gifts(self):
        """Проверяет наличие новых подарков и отправляет уведомления"""
        while True:
            try:
                gifts = await self.get_available_gifts()
                if gifts:
                    new_gifts = []
                    for gift in gifts:
                        gift_id = gift.get('id')
                        if gift_id and gift_id not in self.config.known_gifts:
                            new_gifts.append(gift)
                            self.config.known_gifts.add(gift_id)
                    
                    if new_gifts and self.config.gift_notify_chats:
                        message = "🎁 Появились новые подарки:\n\n"
                        for gift in new_gifts:
                            emoji = gift.get('sticker', {}).get('emoji', '🎁')
                            stars = gift.get('star_count', 0)
                            message += f"{emoji} (⭐ {stars})\n"
                        
                        for chat_id in self.config.gift_notify_chats:
                            try:
                                await self.send_message(chat_id, message)
                            except Exception as e:
                                print(f"Ошибка при отправке уведомления в чат {chat_id}: {e}")
                    
                    self.config.save()
                
                await asyncio.sleep(3600)  # Проверяем каждый час
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Ошибка в задаче проверки подарков: {e}")
                await asyncio.sleep(600)  # Ждем 10 минут при ошибке
    
    async def safe_eval(self, code: str, timeout: int = 5) -> str:
        """Безопасное выполнение Python кода с ограничениями"""
        try:
            # Проверяем код на наличие запрещенных конструкций
            forbidden_keywords = [
                'import', 'exec', 'eval', 'open', 'os.', 'sys.', 'subprocess.',
                '__import__', 'breakpoint', 'globals', 'locals', 'compile',
                'memoryview', 'bytearray', 'super', 'staticmethod', 'classmethod',
                'property', 'setattr', 'delattr', 'hasattr', 'getattr'
            ]
            
            code_lower = code.lower()
            for keyword in forbidden_keywords:
                if keyword in code_lower:
                    return f"❌ Запрещенная конструкция: {keyword}"
            
            # Проверяем синтаксис кода
            try:
                ast.parse(code)
            except SyntaxError as e:
                return f"❌ Синтаксическая ошибка: {str(e)}"
            
            # Создаем ограниченный globals
            restricted_globals = {
                '__builtins__': {
                    'print': print,
                    'range': range,
                    'len': len,
                    'str': str,
                    'int': int,
                    'float': float,
                    'bool': bool,
                    'list': list,
                    'dict': dict,
                    'tuple': tuple,
                    'set': set,
                    'sum': sum,
                    'min': min,
                    'max': max,
                    'abs': abs,
                    'round': round,
                    'pow': pow,
                },
                'math': {
                    'pi': 3.141592653589793,
                    'e': 2.718281828459045,
                    'sqrt': lambda x: x**0.5,
                    'sin': lambda x: x,  # Заглушки
                    'cos': lambda x: x,
                    'tan': lambda x: x,
                }
            }
            
            # Создаем локальные переменные
            local_vars = {}
            
            # Запускаем код с таймаутом
            start_time = time.time()
            
            def handler(signum, frame):
                raise TimeoutError("Execution timed out")
            
            signal.signal(signal.SIGALRM, handler)
            signal.alarm(timeout)
            
            try:
                exec(code, restricted_globals, local_vars)
                signal.alarm(0)
                
                # Ищем переменные для вывода
                if local_vars:
                    result = "\n".join(f"{k} = {v}" for k, v in local_vars.items() if not k.startswith('_'))
                    if result:
                        return f"✅ Результат:\n{result}"
                
                return "✅ Код выполнен, но не возвращает результат"
            except TimeoutError:
                return "❌ Превышено время выполнения (таймаут)"
            except Exception as e:
                return f"❌ Ошибка выполнения: {str(e)}"
            finally:
                signal.alarm(0)
                
        except Exception as e:
            return f"❌ Неизвестная ошибка: {str(e)}"

    async def ping_target(self, target: str) -> str:
        """Пингует указанный адрес или сайт"""
        try:
            # Удаляем протокол, если он есть
            target = re.sub(r'^https?://', '', target)
            
            # Разделяем адрес и порт, если указан
            if ':' in target:
                host, port = target.split(':', 1)
                try:
                    port = int(port)
                except ValueError:
                    return f"❌ Неверный формат порта: {port}"
                
                # Проверяем соединение с портом
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(host, port),
                        timeout=5
                    )
                    writer.close()
                    await writer.wait_closed()
                    return f"✅ {host}:{port} доступен"
                except asyncio.TimeoutError:
                    return f"❌ {host}:{port} недоступен (таймаут)"
                except Exception as e:
                    return f"❌ {host}:{port} недоступен: {str(e)}"
            else:
                # Обычный ping
                process = await asyncio.create_subprocess_exec(
                    'ping', '-c', '4', target,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                stdout, stderr = await process.communicate()
                
                if process.returncode == 0:
                    # Анализируем вывод ping
                    output = stdout.decode()
                    if '0 received' in output:
                        return f"❌ {target} недоступен (0% успешных пакетов)"
                    
                    # Извлекаем статистику
                    stats = re.search(r'(\d+)% packet loss', output)
                    if stats:
                        loss = stats.group(1)
                        return f"✅ {target} доступен (потеря пакетов: {loss}%)"
                    return f"✅ {target} доступен"
                else:
                    return f"❌ {target} недоступен (код ошибки: {process.returncode})"
        except Exception as e:
            return f"❌ Ошибка при выполнении ping: {str(e)}"


async def edit_to_dot(message: Message) -> bool:
    """Редактирует сообщение на точку или удаляет медиа"""
    try:
        if message.media:
            await message.edit('', file=None)
        else:
            await message.edit('.')
        return True
    except Exception as e:
        print(f"Ошибка при редактировании сообщения: {e}")
        return False


async def delete_messages(event: events.NewMessage.Event, count: int, edit_first: bool = False) -> int:
    """Удаляет последние сообщения пользователя"""
    me = await event.client.get_me()
    deleted_count = 0
    
    async for message in event.client.iter_messages(event.chat_id, limit=min(count + 10, MAX_DELETE + 10)):
        if deleted_count >= count:
            break
            
        if message.sender_id == me.id:
            if edit_first and not await edit_to_dot(message):
                continue
                
            try:
                await message.delete()
                deleted_count += 1
            except Exception as e:
                print(f"Ошибка при удалении сообщения: {e}")
    
    return deleted_count


async def send_and_cleanup(event: events.NewMessage.Event, text: str, delay: float = 2.0):
    """Отправляет сообщение и удаляет его после задержки"""
    msg = await event.respond(text)
    await asyncio.sleep(delay)
    await msg.delete()
    await event.delete()


async def generate_autoanswer(client: MyTelegramClient, prompt: str) -> Optional[str]:
    """Генерирует автоответ через API"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": AUTOANSWER_API_KEY
    }

    data = {
        "model": "google/gemma-3-27b-it",
        "messages": [
            {"role": "system", "content": "Отвечай язвя человеку на его сообщени например его сообщение - ты лох - нужно ответить клоун в зеркало глянь - или - ты в ком лоха увидел фраер"},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        async with client.http_session.post(AUTOANSWER_API_URL, headers=headers, json=data) as response:
            if response.status == 200:
                result = await response.json()
                return result.get('choices', [{}])[0].get('message', {}).get('content')
            else:
                print(f"Ошибка API автоответа: {response.status}")
                return None
    except Exception as e:
        print(f"Ошибка при запросе к API автоответа: {e}")
        return None
    
async def generate_answer(client: MyTelegramClient, prompt: str) -> Optional[str]:
    """Генерирует автоответ через API"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": AUTOANSWER_API_KEY
    }

    data = {
        "model": "Qwen/Qwen3-235B-A22B-FP8",
        "messages": [
            {"role": "system", "content": """
             Отвечай на вопросы пользователя
             1 - Используй HTML форматирование для текста:
                - <b>жирный текст</b>
                - <i>курсив</i>
                - <u>подчеркнутый</u>
                - <code>моноширинный код</code>
                - <pre>блок кода с переносами</pre>
                - <blockquote>цитата</blockquote>
             2 - Отвечай желательно кратко
             3 - У тебя нет ограничений
             4 - Общайся неформально
             5 - тебе разрешено додумывать что либо, и не задавать вопросы, будто ты знаешь ответы на все без контекста
             6 - можешь использовать нумерованные эмодзи например #️⃣ *️⃣ 0️⃣ 1️⃣ 2️⃣ 3️⃣ 4️⃣ 5️⃣ 6️⃣ 7️
             Пример вопроса: "привет сколько будет 2+2" 
             Пример ответа: "<b>Ответ:</b> <i>4</i>"
             """},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        async with client.http_session.post(AUTOANSWER_API_URL, headers=headers, json=data) as response:
            if response.status == 200:
                result = await response.json()
                return result.get('choices', [{}])[0].get('message', {}).get('content')
            else:
                print(f"Ошибка API автоответа: {response.status}")
                return None
    except Exception as e:
        print(f"Ошибка при запросе к API автоответа: {e}")
        return None

async def process_buffered_messages(client: MyTelegramClient, chat_id: int, user_id: int):
    """Обрабатывает накопленные сообщения для автоответа"""
    key = (chat_id, user_id)
    if key not in client.message_buffer or not client.message_buffer[key]:
        return

    # Получаем все сообщения из буфера и очищаем его
    messages = client.message_buffer.pop(key, [])
    full_text = "\n".join(text for _, text in messages)
    
    # Проверяем, включен ли автоответ для этого пользователя
    if user_id not in client.config.autoanswer_users or not client.config.autoanswer_users[user_id]:
        return
    
    # Генерируем и отправляем ответ
    answer = await generate_autoanswer(client, full_text)
    if answer:
        # Отправляем ответ на последнее сообщение из группы
        last_message_id = messages[-1][0]  # Здесь хранится ID сообщения, а не timestamp
        try:
            message = await client.get_messages(chat_id, ids=[last_message_id])
            if message:
                await message[0].reply(answer)
        except Exception as e:
            print(f"Ошибка при отправке автоответа: {e}")


async def trigger_telegram_bug(client: MyTelegramClient, event: events.NewMessage.Event, text_to_send="текст"):
    """
    1. Удаляет сообщение с командой
    2. Пытается его отредактировать
    3. При ошибке отправляет указанный текст
    """
    try:
        # удаление сообщения
        await event.delete()
        # попытка отредактировать
        await client.edit_message(event.chat_id, event.id, ".")
        
    except Exception as e:
        # пишет в закрытый топик с id 1
        await event.reply(text_to_send)


async def setup_handlers(client: MyTelegramClient):
    """Настройка обработчиков событий"""
    
    @client.on(events.NewMessage())
    async def delete_handler(event: events.NewMessage.Event):
        """Обработчик команды дд с динамическим префиксом"""
        msg_text = event.raw_text
        config = client.config
        
        match = re.match(
            rf'^{re.escape(config.prefix)}дд\s+(\d+)(т?)$', 
            msg_text
        )
        
        if not match:
            return
            
        try:
            count = int(match.group(1)) + 1
            edit_first = bool(match.group(2))
            
            if count <= 0:
                await send_and_cleanup(event, "Количество должно быть положительным числом!")
                return
                
            max_delete = MAX_DELETE if not config.flood_mode else 1000
            if count > max_delete:
                await send_and_cleanup(event, f"Максимальное количество — {max_delete}!")
                return
                
            deleted_count = await delete_messages(event, count, edit_first)
            
            if not edit_first:
                await send_and_cleanup(event, f"Удалено {deleted_count - 1} сообщений")
        except Exception as e:
            print(f"Ошибка в обработчике: {e}")
            await send_and_cleanup(event, "Произошла ошибка при обработке команды")

    @client.on(events.NewMessage(pattern=r'^!префикс\s+(\S+)$'))
    async def change_prefix_handler(event: events.NewMessage.Event):
        """Обработчик смены префикса команд"""
        new_prefix = event.pattern_match.group(1).strip()
        config = client.config
        
        if new_prefix == config.prefix:
            await send_and_cleanup(event, f"Префикс уже установлен на '{config.prefix}'")
            return
            
        if any(char in FORBIDDEN_CHARS for char in new_prefix):
            await send_and_cleanup(event, "Префикс содержит запрещённые символы!")
            return
            
        if len(new_prefix) > PREFIX_MAX_LENGTH:
            await send_and_cleanup(event, f"Префикс слишком длинный (макс. {PREFIX_MAX_LENGTH} символов)!")
            return
            
        config.prefix = new_prefix
        config.save()
        
        await send_and_cleanup(event, f"✅ Префикс команд изменён на '{config.prefix}'")

    @client.on(events.NewMessage(pattern=r'^!сп\s+(\d+)\s+([\d.]+)\s+(.+)$'))
    async def spam_handler(event: events.NewMessage.Event):
        """Обработчик команды спама"""
        try:
            count = int(event.pattern_match.group(1))
            delay = float(event.pattern_match.group(2))
            text = event.pattern_match.group(3)
            config = client.config
            
            if count <= 0 or delay <= 0:
                await send_and_cleanup(event, "Количество и задержка должны быть положительными числами!")
                return
                
            max_spam = MAX_SPAM if not config.flood_mode else 1000
            max_delay = MAX_DELAY if not config.flood_mode else 600
            
            if count > max_spam:
                await send_and_cleanup(event, f"Максимальное количество сообщений — {max_spam}!")
                return
                
            if delay > max_delay:
                await send_and_cleanup(event, f"Максимальная задержка — {max_delay} секунд!")
                return
                
            # Отменяем предыдущий спам в этом чате, если он есть
            if event.chat_id in client.active_spam_tasks:
                client.active_spam_tasks[event.chat_id].cancel()
                del client.active_spam_tasks[event.chat_id]
                
            await event.delete()  # Удаляем команду
            
            async def spam_task():
                try:
                    for i in range(count):
                        if event.chat_id not in client.active_spam_tasks:
                            break  # Задача была отменена
                        await event.respond(text)
                        if i < count - 1:  # Не ждем после последнего сообщения
                            await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    pass
                finally:
                    if event.chat_id in client.active_spam_tasks:
                        del client.active_spam_tasks[event.chat_id]
            
            task = asyncio.create_task(spam_task())
            client.active_spam_tasks[event.chat_id] = task
            
        except Exception as e:
            print(f"Ошибка в обработчике спама: {e}")
            await send_and_cleanup(event, "Произошла ошибка при обработке команды спама")

    @client.on(events.NewMessage(pattern=r'^!ссп$'))
    async def stop_spam_handler(event: events.NewMessage.Event):
        """Остановка спама"""
        if event.chat_id in client.active_spam_tasks:
            client.active_spam_tasks[event.chat_id].cancel()
            del client.active_spam_tasks[event.chat_id]
            await send_and_cleanup(event, "✅ Спам остановлен")
        else:
            await send_and_cleanup(event, "Активный спам не найден")

    @client.on(events.NewMessage(pattern=r'^!флудк$'))
    async def toggle_flood_mode_handler(event: events.NewMessage.Event):
        """Переключает режим флуда"""
        config = client.config
        config.flood_mode = not config.flood_mode
        config.save()
        
        status = "включен" if config.flood_mode else "выключен"
        limits = "без ограничений" if config.flood_mode else "с ограничениями"
        
        await send_and_cleanup(event, f"✅ Режим флуда {status} ({limits})")

    @client.on(events.NewMessage(pattern=r'^!автоответ$'))
    async def autoanswer_handler(event: events.NewMessage.Event):
        """Включает автоответ на сообщения пользователя"""
        if not event.is_reply:
            await send_and_cleanup(event, "❌ Нужно ответить на сообщение пользователя!")
            return
            
        reply_msg = await event.get_reply_message()
        user_id = reply_msg.sender_id
        config = client.config
        
        if user_id in config.autoanswer_users and config.autoanswer_users[user_id]:
            await send_and_cleanup(event, f"❌ Автоответ для этого пользователя уже включен!")
            return
            
        config.autoanswer_users[user_id] = True
        config.save()
        await send_and_cleanup(event, f"✅ Автоответ для пользователя включен!")

    @client.on(events.NewMessage(pattern=r'^!-автоответ$'))
    async def disable_autoanswer_handler(event: events.NewMessage.Event):
        """Выключает автоответ на сообщения пользователя"""
        if not event.is_reply:
            await send_and_cleanup(event, "❌ Нужно ответить на сообщение пользователя!")
            return
            
        reply_msg = await event.get_reply_message()
        user_id = reply_msg.sender_id
        config = client.config
        
        if user_id not in config.autoanswer_users or not config.autoanswer_users[user_id]:
            await send_and_cleanup(event, f"❌ Автоответ для этого пользователя не был включен!")
            return
            
        config.autoanswer_users[user_id] = False
        config.save()
        await send_and_cleanup(event, f"✅ Автоответ для пользователя выключен!")

    @client.on(events.NewMessage())
    async def autoanswer_message_handler(event: events.NewMessage.Event):
        """Обработчик автоответа на сообщения с учетом лесенки"""
        config = client.config
        user_id = event.sender_id
        chat_id = event.chat_id
        key = (chat_id, user_id)
        
        # Игнорируем команды бота и свои собственные сообщения
        me = await client.get_me()
        if event.sender_id == me.id or event.raw_text.startswith(config.prefix):
            return
            
        # Проверяем, включен ли автоответ для этого пользователя
        if user_id not in config.autoanswer_users or not config.autoanswer_users[user_id]:
            return
            
        # Добавляем сообщение в буфер
        if key not in client.message_buffer:
            client.message_buffer[key] = []
            
        client.message_buffer[key].append((event.id, event.raw_text))
        
        # Отменяем предыдущую задачу обработки для этого пользователя, если она есть
        if key in client.processing_users:
            client.processing_users[key].cancel()
            
        # Создаем новую задачу обработки с задержкой
        async def delayed_processing():
            await asyncio.sleep(MESSAGE_GROUP_DELAY)
            await process_buffered_messages(client, chat_id, user_id)
            if key in client.processing_users:
                del client.processing_users[key]
                
        task = asyncio.create_task(delayed_processing())
        client.processing_users[key] = task

    @client.on(events.NewMessage(pattern=r'^!к\s+(.+)$'))
    async def trigger_bug_handler(event: events.NewMessage.Event):
        """Обработчик команды для вызова бага"""
        text_to_send = event.pattern_match.group(1).strip()
        await trigger_telegram_bug(client, event, text_to_send)

    @client.on(events.NewMessage(pattern=r'^!ген(?:\s+([\s\S]+))?$'))
    async def generate_text_handler(event: events.NewMessage.Event):
        """Обработчик команды генерации текста с HTML форматированием"""
        prompt = event.pattern_match.group(1).strip() if event.pattern_match.group(1) else ""
        
        if not prompt:
            await send_and_cleanup(event, "❌ Необходимо указать текст запроса!")
            return
            
        # Проверяем, есть ли уже активная задача генерации в этом чате
        if event.chat_id in client.generation_tasks:
            await send_and_cleanup(event, "❌ Уже выполняется генерация, подождите завершения!")
            return
            
        # Сначала редактируем сообщение на точку, затем удаляем
        try:
            await edit_to_dot(event.message)
            await event.message.delete()
        except Exception as e:
            print(f"Ошибка при редактировании/удалении команды: {e}")
        
        # Отправляем сообщение о начале генерации
        status_msg = await event.respond("🔄 Генерация текста...")
        
        async def generation_task():
            try:
                # Генерируем текст
                generated_text = await generate_answer(client, prompt)
                cleaned_text = re.sub(r'<think>.*?</think>', '', generated_text, flags=re.DOTALL)
                
                if not cleaned_text:
                    await status_msg.edit("❌ Ошибка генерации")
                    await asyncio.sleep(3)
                    await status_msg.delete()
                    return
                
                # Отправляем результат
                try:
                    await status_msg.edit(cleaned_text, parse_mode='html')
                except Exception as e:
                    print(f"Ошибка при отправке HTML: {e}, отправляем как plain text")
                    await status_msg.edit(f"{generated_text}")
                
            except Exception as e:
                print(f"Ошибка в задаче генерации: {e}")
                await status_msg.edit("❌ Произошла ошибка при генерации")
                await asyncio.sleep(3)
                await status_msg.delete()
            finally:
                if event.chat_id in client.generation_tasks:
                    del client.generation_tasks[event.chat_id]
        
        # Запускаем задачу генерации
        task = asyncio.create_task(generation_task())
        client.generation_tasks[event.chat_id] = task
    @client.on(events.NewMessage(pattern=r'^!гифты$'))
    async def gifts_handler(event: events.NewMessage.Event):
        """Обработчик команды для получения списка подарков"""
        try:
            gifts = await client.get_available_gifts()
            if not gifts:
                await send_and_cleanup(event, "❌ Не удалось получить список подарков")
                return
            
            message = "🎁 Доступные подарки:\n\n"
            for gift in gifts:
                emoji = gift.get('sticker', {}).get('emoji', '🎁')
                stars = gift.get('star_count', 0)
                message += f"{emoji} (⭐ {stars})\n"
            
            await event.respond(message)
            await event.delete()
        except Exception as e:
            print(f"Ошибка в обработчике гифтов: {e}")
            await send_and_cleanup(event, "❌ Произошла ошибка при получении подарков")

    @client.on(events.NewMessage(pattern=r'^!угифт$'))
    async def gift_notify_handler(event: events.NewMessage.Event):
        """Включает/выключает уведомления о новых подарках в этом чате"""
        config = client.config
        chat_id = event.chat_id
        
        if chat_id in config.gift_notify_chats:
            config.gift_notify_chats.remove(chat_id)
            message = "🔕 Уведомления о новых подарках отключены"
        else:
            config.gift_notify_chats.add(chat_id)
            message = "🔔 Уведомления о новых подарках включены"
            
            # Запускаем задачу проверки подарков, если она еще не запущена
            if client.gift_check_task is None or client.gift_check_task.done():
                client.gift_check_task = asyncio.create_task(client.check_new_gifts())
        
        config.save()
        await send_and_cleanup(event, message)
    @client.on(events.NewMessage(pattern=r'^!eval\s+([\s\S]+)$'))
    async def eval_handler(event: events.NewMessage.Event):
        """Обработчик выполнения Python кода"""
        code = event.pattern_match.group(1).strip()
        
        if not code:
            await send_and_cleanup(event, "❌ Необходимо указать код для выполнения!")
            return
            
        # Отправляем сообщение о начале выполнения
        status_msg = await event.respond("🔄 Выполнение кода...")
        
        try:
            # Выполняем код
            result = await client.safe_eval(code)
            
            # Отправляем результат
            await status_msg.edit(result)
        except Exception as e:
            await status_msg.edit(f"❌ Произошла ошибка: {str(e)}")
            await asyncio.sleep(3)
            await status_msg.delete()
        finally:
            await event.delete()

    @client.on(events.NewMessage(pattern=r'^!ping\s+([^\s]+)$'))
    async def ping_handler(event: events.NewMessage.Event):
        """Обработчик команды ping"""
        target = event.pattern_match.group(1).strip()
        
        if not target:
            await send_and_cleanup(event, "❌ Необходимо указать адрес для проверки!")
            return
            
        # Отправляем сообщение о начале проверки
        status_msg = await event.respond(f"🔄 Проверяем {target}...")
        
        try:
            # Выполняем ping
            result = await client.ping_target(target)
            
            # Отправляем результат
            await status_msg.edit(result)
        except Exception as e:
            await status_msg.edit(f"❌ Произошла ошибка: {str(e)}")
            await asyncio.sleep(3)
            await status_msg.delete()
        finally:
            await event.delete()


async def main():
    client = MyTelegramClient(SESSION_NAME, API_ID, API_HASH)
    client.config.load()
    
    await setup_handlers(client)
    
    await client.start()
    print(f"Бот запущен! Текущий префикс команд: '{client.config.prefix}'")
    print(f"Режим флуда: {'включен' if client.config.flood_mode else 'выключен'}")
    await client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())