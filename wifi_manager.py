import subprocess
import shutil
import threading
import time
from flask import Flask, render_template_string, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = "wifi_manager_secret"

# Проверка наличия nmcli в системе
NMCLI_PATH = shutil.which("nmcli")
IS_LOCAL_DEV = NMCLI_PATH is None

def get_wifi_networks():
    """Получает список доступных Wi-Fi сетей."""
    if IS_LOCAL_DEV:
        return [
            {"ssid": "Mock_WiFi_1", "signal": "90", "security": "WPA2"},
            {"ssid": "Home_Router", "signal": "75", "security": "WPA2"},
            {"ssid": "Coffee_Shop", "signal": "40", "security": "NONE"},
        ]

    try:
        # -t (terse): лаконичный вывод для парсинга
        # -f SSID,SIGNAL,SECURITY: выбор конкретных полей
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
            capture_output=True,
            text=True,
            check=True
        )
        
        networks = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split(':')
            if len(parts) >= 3:
                security = parts[-1]
                signal = parts[-2]
                ssid = ":".join(parts[:-2])
                
                if ssid: 
                    networks.append({
                        "ssid": ssid,
                        "signal": signal,
                        "security": security
                    })
        return networks
    except subprocess.CalledProcessError as e:
        print(f"Ошибка при сканировании: {e}")
        return []

def ensure_hotspot_mode():
    """Проверяет наличие подключения и создает Hotspot, если сети нет."""
    print("Проверка состояния Wi-Fi...")
    
    if IS_LOCAL_DEV:
        print("[MOCK] Проверка активных подключений...")
        print("[MOCK] Нет активного Wi-Fi подключения. Создание Hotspot 'RPI-Setup'...")
        print("[MOCK] Hotspot активирован. IP адрес: 10.42.0.1")
        return

    try:
        # Проверяем, есть ли активное Wi-Fi подключение
        result = subprocess.run(
            ["nmcli", "-t", "-f", "TYPE,STATE", "connection", "show", "--active"],
            capture_output=True,
            text=True
        )
        
        # Ищем 802-11-wireless или wifi в выводе
        if "802-11-wireless:activated" in result.stdout or "wifi:activated" in result.stdout:
            print("Обнаружено активное Wi-Fi подключение. Hotspot не нужен.")
            return

        print("Активное Wi-Fi подключение не найдено. Создание точки доступа (Hotspot)...")

        # Удаляем старый профиль Hotspot, если он есть, чтобы не было конфликтов
        subprocess.run(["nmcli", "con", "delete", "Hotspot"], capture_output=True)

        # Создаем новое подключение Hotspot
        # 802-11-wireless.mode ap = Access Point
        # ipv4.method shared = Раздача интернета (создает шлюз 10.42.0.1)
        # wifi-sec.key-mgmt none = Без пароля (Open)
        
        # 1. Создаем базовое подключение
        subprocess.run([
            "nmcli", "con", "add", "type", "wifi", "ifname", "wlan0", "con-name", "Hotspot",
            "autoconnect", "yes", "ssid", "RPI-Setup"
        ], check=True)

        # 2. Настраиваем режим AP и IP
        subprocess.run([
            "nmcli", "con", "modify", "Hotspot",
            "802-11-wireless.mode", "ap", 
            "ipv4.method", "shared"
        ], check=True)

        # 3. Поднимаем интерфейс
        subprocess.run(["nmcli", "con", "up", "Hotspot"], check=True)
        
        print("Hotspot 'RPI-Setup' успешно создан и активирован.")
        print("Подключитесь к сети 'RPI-Setup' и перейдите по адресу: http://10.42.0.1:5000")

    except subprocess.CalledProcessError as e:
        print(f"Ошибка при настройке Hotspot: {e}")
    except Exception as e:
        print(f"Непредвиденная ошибка: {e}")

def run_nmcli_connect(ssid, password):
    """Фоновая функция для выполнения подключения."""
    time.sleep(2) # Задержка, чтобы Flask успел отправить ответ браузеру
    
    if IS_LOCAL_DEV:
        print(f"[MOCK] Выполнение команды: sudo nmcli dev wifi connect '{ssid}' password '{password}'")
        return

    try:
        # Используем sudo nmcli для подключения
        result = subprocess.run(
            ["sudo", "nmcli", "dev", "wifi", "connect", ssid, "password", password],
            capture_output=True,
            text=True,
            timeout=60 # Увеличим таймаут на всякий случай
        )
        if result.returncode == 0:
            print(f"Успешное подключение к {ssid}")
            
            # Если подключение успешно, удаляем/отключаем Hotspot, чтобы переключиться в режим клиента
            try:
                print("Удаление профиля Hotspot для перехода в режим клиента...")
                subprocess.run(["sudo", "nmcli", "con", "delete", "Hotspot"], capture_output=True)
            except Exception as e:
                print(f"Ошибка при удалении Hotspot: {e}")
                
        else:
            print(f"Ошибка подключения к {ssid}: {result.stderr}")
            # Можно добавить логику возврата к Hotspot, если подключение не удалось
            
    except Exception as e:
        print(f"Исключение при попытке подключения: {e}")

# HTML шаблон
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Raspberry Pi Wi-Fi Manager</title>
    <style>
        body { font-family: sans-serif; margin: 20px; background-color: #f4f4f9; }
        h1 { color: #333; }
        table { width: 100%; border-collapse: collapse; margin-bottom: 20px; background: #fff; }
        th, td { padding: 12px; border: 1px solid #ddd; text-align: left; }
        th { background-color: #007bff; color: white; }
        .form-container { background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .alert { padding: 15px; margin-bottom: 20px; border-radius: 4px; border: 1px solid transparent; }
        .alert-success { background-color: #d4edda; color: #155724; border-color: #c3e6cb; }
        .alert-danger { background-color: #f8d7da; color: #721c24; border-color: #f5c6cb; }
        .alert-info { background-color: #d1ecf1; color: #0c5460; border-color: #bee5eb; }
        .btn { background-color: #007bff; color: white; padding: 10px 15px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }
        .btn:hover { background-color: #0056b3; }
        input[type="text"], input[type="password"] { width: 100%; padding: 10px; margin: 5px 0 15px 0; display: inline-block; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
    </style>
</head>
<body>
    <h1>Управление Wi-Fi (Raspberry Pi 5)</h1>
    
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for category, message in messages %}
          <div class="alert alert-{{ 'danger' if category == 'error' else ('info' if category == 'info' else 'success') }}">
            {{ message }}
          </div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <table>
        <thead>
            <tr>
                <th>SSID</th>
                <th>Сигнал (%)</th>
                <th>Безопасность</th>
                <th>Действие</th>
            </tr>
        </thead>
        <tbody>
            {% for net in networks %}
            <tr>
                <td>{{ net.ssid }}</td>
                <td>{{ net.signal }}</td>
                <td>{{ net.security }}</td>
                <td>
                    <button class="btn" onclick="selectNetwork('{{ net.ssid }}')">Выбрать</button>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>

    <div class="form-container">
        <h2>Подключение к сети</h2>
        <form action="/connect" method="post">
            <label for="ssid_input">SSID:</label>
            <input type="text" name="ssid" id="ssid_input" required placeholder="Выберите сеть из списка или введите SSID">
            
            <label for="password_input">Пароль:</label>
            <input type="password" name="password" id="password_input" required placeholder="Введите пароль">
            
            <button type="submit" class="btn" style="width: 100%; font-weight: bold;">Подключиться</button>
        </form>
    </div>

    <script>
        function selectNetwork(ssid) {
            document.getElementById('ssid_input').value = ssid;
            document.getElementById('password_input').focus();
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    networks = get_wifi_networks()
    return render_template_string(HTML_TEMPLATE, networks=networks)

@app.route('/connect', methods=['POST'])
def connect():
    ssid = request.form.get('ssid')
    password = request.form.get('password')
    
    if not ssid:
        flash("SSID не может быть пустым", "error")
        return redirect(url_for('index'))
    
    # Сразу проверяем пароль в nmcli мы не можем без запуска, 
    # но мы можем запустить процесс в фоне, чтобы интерфейс не завис.
    
    # В реальности для Raspberry Pi нам нужно отправить ответ ДО того, 
    # как интерфейс переподключится и связь может пропасть.
    
    thread = threading.Thread(target=run_nmcli_connect, args=(ssid, password))
    thread.daemon = True
    thread.start()
    
    flash(f"Подключение к {ssid}... Устройство может временно уйти в офлайн.", "info")
    return redirect(url_for('index'))

if __name__ == '__main__':
    host = '0.0.0.0'
    port = 5000
    print(f"Запуск Wi-Fi менеджера на http://{host}:{port}")
    if IS_LOCAL_DEV:
        print("ВНИМАНИЕ: nmcli не найден. Работает режим локальной разработки (MOCK).")

    # 4. Проверяем режим при запуске
    ensure_hotspot_mode()

    app.run(host=host, port=port, debug=True)