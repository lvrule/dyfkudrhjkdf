# client.py - исправленная версия
import requests
import socket
import time
import platform
import hashlib
from threading import Thread
import uuid
import sys

# Конфигурация
SERVER_URL = "http://localhost:8080"  # Тестируем на локальной машине
API_SECRET = "6ff39f9e75475d447411994e94a080c3"    # Должен совпадать с серверным
UNIQUE_SUFFIX = "PCCTRL"

def generate_device_id():
    """Генерирует уникальный ID устройства"""
    pc_name = platform.node()
    unique_hash = hashlib.md5(str(uuid.getnode()).encode()).hexdigest()[:6].upper()
    device_id = f"{pc_name}-{UNIQUE_SUFFIX}-{unique_hash}"
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
                    headers={"X-Auth-Token": API_SECRET},
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
    
    def send_heartbeat(self):
        """Регулярная отправка сигналов активности"""
        while self.running:
            try:
                response = requests.post(
                    f"{SERVER_URL}/heartbeat",
                    json={"device_id": self.device_id},
                    headers={"X-Auth-Token": API_SECRET},
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
                    headers={"X-Auth-Token": API_SECRET},
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
            # Здесь реализуйте выполнение команд
            # Например:
            if command['command'] == 'screenshot':
                self.take_screenshot()
            elif command['command'] == 'shutdown':
                self.shutdown_pc()
                
        except Exception as e:
            print(f"Ошибка выполнения команды: {e}")
    
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