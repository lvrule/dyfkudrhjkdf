# client.py - усовершенствованная версия
import requests
import socket
import time
import platform
import hashlib
from threading import Thread
import uuid

# Конфигурация
SERVER_URL = "http://95.163.84.18:8080"
UNIQUE_SUFFIX = "PCCTRL"  # Ваша уникальная приписка

def generate_device_id():
    """Генерирует уникальный ID устройства на основе имени ПК и хеша"""
    # Получаем имя компьютера
    pc_name = platform.node()
    
    # Генерируем дополнительный уникальный идентификатор
    unique_hash = hashlib.md5(str(uuid.getnode()).encode()).hexdigest()[:6].upper()
    
    # Собираем итоговый ID (имя_ПК + приписка + хеш)
    device_id = f"{pc_name}-{UNIQUE_SUFFIX}-{unique_hash}"
    
    # Убираем возможные пробелы и спецсимволы
    device_id = ''.join(e for e in device_id if e.isalnum() or e == '-')
    
    return device_id

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
                    
            except Exception as e:
                print(f"Ошибка регистрации: {e}. Повтор через 30 сек...")
                time.sleep(30)
                
        return False
    
    def send_heartbeat(self):
        """Регулярная отправка сигналов активности"""
        while self.running:
            try:
                requests.post(
                    f"{SERVER_URL}/heartbeat",
                    json={"device_id": self.device_id},
                    timeout=5
                )
                time.sleep(60)  # Отправляем каждую минуту
                
            except Exception as e:
                print(f"Ошибка heartbeat: {e}")
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
                    commands = response.json()
                    for cmd in commands:
                        self.execute_command(cmd)
                
                time.sleep(5)  # Проверяем команды каждые 5 секунд
                
            except Exception as e:
                print(f"Ошибка проверки команд: {e}")
                time.sleep(10)
    
    def execute_command(self, command):
        """Выполнение полученной команды"""
        print(f"Выполнение команды: {command}")
        # Здесь реализуйте выполнение команд
    
    def run(self):
        """Запуск клиента"""
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
    client = PCClient()
    client.run()