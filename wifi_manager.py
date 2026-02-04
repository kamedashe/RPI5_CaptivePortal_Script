import subprocess
import shutil
import threading
import time
import uuid

from flask import Flask, render_template_string, request, redirect, url_for, flash, Response

app = Flask(__name__)
app.secret_key = "wifi_manager_secret"

# Проверка наличия nmcli в системе
NMCLI_PATH = shutil.which("nmcli")
IS_LOCAL_DEV = NMCLI_PATH is None

def get_device_suffix():
    """
    Генерирует уникальный суффикс из MAC-адреса устройства.
    Пример возврата: 'A1B2' (последние 4 символа MAC)
    """
    try:
        # Получаем MAC адрес как число
        mac_num = uuid.getnode()
        # Преобразуем в hex строку (например: 0123456789ab)
        mac_hex = '{:012x}'.format(mac_num)
        # Берем последние 4 символа и делаем UpperCase
        suffix = mac_hex[-4:].upper()
        return suffix
    except Exception as e:
        print(f"Error getting MAC: {e}")
        return "SETUP" # Фоллбек, если не смогли получить MAC

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
        print(f"Scanning error: {e}")
        return []

def ensure_hotspot_mode():
    """Проверяет наличие подключения и создает Hotspot, если сети нет."""
    print("Checking Wi-Fi status...")
    
    if IS_LOCAL_DEV:
        print("[MOCK] Checking active connections...")
        print("[MOCK] No active Wi-Fi connection. Creating Hotspot 'RPI-Setup'...")
        print("[MOCK] Hotspot activated. IP address: 10.42.0.1")
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
            print("Active Wi-Fi connection detected. Hotspot not needed.")
            return

        print("No active Wi-Fi connection found. Starting Hotspot creation procedure...")

        # --- 1. Полный сброс Radio (User Request: Full reset) ---
        # Выключаем и включаем радио для сброса состояния драйвера
        print("♻️ Resetting Wi-Fi driver (Radio OFF/ON)...")
        subprocess.run(["sudo", "nmcli", "radio", "wifi", "off"], check=True)
        time.sleep(2) 
        subprocess.run(["sudo", "nmcli", "radio", "wifi", "on"], check=True)
        print("⏳ Waiting for Wi-Fi adapter initialization (4 sec)...")
        time.sleep(4)

        # --- 2. Удаление «фантомов» (User Request: Clean wlan0) ---
        print("🧹 Cleaning interface from phantom connections...")
        # После включения радио NM мог автоматом подцепить что-то. Проверяем и удаляем.
        try:
            res_active = subprocess.run(
                ["nmcli", "-t", "-f", "UUID,DEVICE,NAME", "con", "show", "--active"],
                capture_output=True, text=True
            )
            for line in res_active.stdout.strip().split('\n'):
                if not line: continue
                parts = line.split(':') # UUID:DEVICE:NAME
                if len(parts) >= 2:
                    uuid = parts[0]
                    device = parts[1]
                    name = parts[2] if len(parts) > 2 else "Unknown"
                    
                    # Если висит что-то на wlan0 и это не наш целевой Hotspot (которого еще нет)
                    if device == "wlan0" and name != "Hotspot":
                        print(f"🔪 Forcibly disconnecting phantom: {name} ({uuid})")
                        subprocess.run(["sudo", "nmcli", "con", "down", uuid], capture_output=True)
        except Exception as e:
            print(f"⚠️ Error cleaning phantoms (non-critical): {e}")

        # --- 3. Создание Hotspot ---
        print("Creating Access Point (Hotspot)...")

        # Генерируем уникальное имя
        unique_suffix = get_device_suffix()
        ssid_name = f"RPI-Setup-{unique_suffix}"

        print(f"🔥 Creating access point with name: {ssid_name}")

        # Удаляем старый профиль Hotspot, если он есть
        subprocess.run(["sudo", "nmcli", "con", "delete", "Hotspot"], capture_output=True)

        # 1. Создаем базовое подключение
        # Создаем новое подключение с уникальным именем
        subprocess.run([
            "sudo", "nmcli", "con", "add", "type", "wifi", "ifname", "wlan0", "con-name", "Hotspot",
            "autoconnect", "yes", "ssid", ssid_name
        ], check=True)

        # 2. Настраиваем режим AP, IP и строгий WPA2-AES (RSN/CCMP)
        # Это "золотой стандарт" для Apple устройств
        subprocess.run([
            "sudo", "nmcli", "con", "modify", "Hotspot",
            "802-11-wireless.mode", "ap", 
            "802-11-wireless.band", "bg",
            "802-11-wireless.channel", "1",
            "ipv4.method", "shared",
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.proto", "rsn",       # Force WPA2
            "wifi-sec.pairwise", "ccmp",   # Force AES
            "wifi-sec.group", "ccmp",      # Force AES
            "wifi-sec.psk", "Alpina2023!"
        ], check=True)

        # 3. Поднимаем интерфейс
        subprocess.run(["sudo", "nmcli", "con", "up", "Hotspot"], check=True)
        
        print("✅ Hotspot 'RPI-Setup' (WPA2-AES) successfully created and activated.")
        print("Connect to network 'RPI-Setup' and go to: http://10.42.0.1:5000")

    except subprocess.CalledProcessError as e:
        print(f"❌ Error configuring Hotspot: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

def run_nmcli_connect(ssid, password):
    """Фоновая функция для выполнения подключения."""
    time.sleep(2) # Задержка, чтобы Flask успел отправить ответ браузеру
    
    if IS_LOCAL_DEV:
        print(f"[MOCK] Executing command: sudo nmcli dev wifi connect '{ssid}' password '{password}'")
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
            print(f"Successfully connected to {ssid}")
            
            # Если подключение успешно, удаляем/отключаем Hotspot, чтобы переключиться в режим клиента
            try:
                print("Deleting Hotspot profile to switch to client mode...")
                subprocess.run(["sudo", "nmcli", "con", "delete", "Hotspot"], capture_output=True)
            except Exception as e:
                print(f"Error deleting Hotspot: {e}")
                
        else:
            print(f"Error connecting to {ssid}: {result.stderr}")
            # Можно добавить логику возврата к Hotspot, если подключение не удалось
            
    except Exception as e:
        print(f"Exception during connection attempt: {e}")

def check_internet():
    """Проверяет доступность интернета (ping 8.8.8.8)."""
    if IS_LOCAL_DEV:
        return True # В разработке считаем, что интернет есть
    
    try:
        # ping -c 1 (один пакет), -W 2 (таймаут 2 сек)
        subprocess.run(
            ["ping", "-c", "1", "-W", "2", "8.8.8.8"], 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL, 
            check=True
        )
        return True
    except subprocess.CalledProcessError:
        return False
    except Exception as e:
        print(f"Ping check error: {e}")
        return False

def internet_monitor_loop():
    """
    Фоновый процесс:
    1. Если есть интернет (мы подключились к Wi-Fi) -> УБИВАЕМ Hotspot (безопасность).
    2. Если интернета нет -> ПОДНИМАЕМ Hotspot (чтобы юзер мог настроить).
    """
    print("Starting background internet monitoring (Mode: Wi-Fi Provisioning)...")
    while True:
        time.sleep(10) # Проверяем каждые 10 секунд (можно реже, но для тестов лучше так)
        
        if IS_LOCAL_DEV:
            continue

        try:
            # 1. ПРОВЕРКА ИНТЕРНЕТА (Ping)
            # Если мы успешно подключились к клиентскому Wi-Fi, пинг пройдет.
            if check_internet():
                # Логика: "У нас есть интернет, значит Hotspot больше не нужен. Убиваем."
                
                # Проверяем, жив ли еще Hotspot
                res = subprocess.run(
                    ["nmcli", "-t", "-f", "NAME", "con", "show", "--active"], 
                    capture_output=True, text=True
                )
                if "Hotspot" in res.stdout:
                    print("✅ Internet restored! Killing configuration access point (Hotspot)...")
                    subprocess.run(["sudo", "nmcli", "con", "delete", "Hotspot"], capture_output=True)
                
                continue # Всё хорошо, интернет есть, спим дальше
            
            # 2. ЕСЛИ ИНТЕРНЕТА НЕТ
            print("🔴 No internet access. Checking if Hotspot is up...")
            
            # Проверяем, есть ли активные соединения вообще (чтобы не дёргать зря)
            res = subprocess.run(
                 ["nmcli", "-t", "-f", "NAME,TYPE", "con", "show", "--active"], 
                 capture_output=True, text=True
            )
            
            # Если Hotspot уже работает — ничего не делаем, ждем пользователя
            if "Hotspot" in res.stdout:
                continue

            # Если Hotspot нет и интернета нет — значит мы отвалились.
            # Надо поднимать точку спасения.
            
            # Сначала убиваем попытки подключения к другим сетям, чтобы освободить адаптер
            for line in res.stdout.strip().split('\n'):
                if "wifi" in line or "wireless" in line:
                    conn_name = line.split(':')[0]
                    print(f"Cancelling connection attempts to {conn_name} to start Hotspot...")
                    subprocess.run(["sudo", "nmcli", "con", "down", conn_name])

            # Запускаем Hotspot
            ensure_hotspot_mode()

        except Exception as e:
            print(f"Error in monitor loop: {e}")

def start_monitor_thread():
    thread = threading.Thread(target=internet_monitor_loop, daemon=True)
    thread.start()



# HTML шаблон
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
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
    <h1>Wi-Fi Manager (Raspberry Pi 5)</h1>
    
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
                <th>Signal (%)</th>
                <th>Security</th>
                <th>Action</th>
            </tr>
        </thead>
        <tbody>
            {% for net in networks %}
            <tr>
                <td>{{ net.ssid }}</td>
                <td>{{ net.signal }}</td>
                <td>{{ net.security }}</td>
                <td>
                    <button class="btn" onclick="selectNetwork('{{ net.ssid }}')">Select</button>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>

    <div class="form-container">
        <h2>Connect to Network</h2>
        <form action="/connect" method="post">
            <label for="ssid_input">SSID:</label>
            <input type="text" name="ssid" id="ssid_input" required placeholder="Select a network or enter SSID">
            
            <label for="password_input">Password:</label>
            <input type="password" name="password" id="password_input" required placeholder="Enter password">
            
            <button type="submit" class="btn" style="width: 100%; font-weight: bold;">Connect</button>
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
        flash("SSID cannot be empty", "error")
        return redirect(url_for('index'))
    
    # Сразу проверяем пароль в nmcli мы не можем без запуска, 
    # но мы можем запустить процесс в фоне, чтобы интерфейс не завис.
    
    # В реальности для Raspberry Pi нам нужно отправить ответ ДО того, 
    # как интерфейс переподключится и связь может пропасть.
    
    thread = threading.Thread(target=run_nmcli_connect, args=(ssid, password))
    thread.daemon = True
    thread.start()
    
    flash(f"Connecting to {ssid}... Device may go offline temporarily.", "info")
    return redirect(url_for('index'))

if __name__ == '__main__':
    host = '0.0.0.0'
    port = 5000
    print(f"Starting Wi-Fi Manager at http://{host}:{port}")
    if IS_LOCAL_DEV:
        print("WARNING: nmcli not found. Running in local development mode (MOCK).")

    # 4. Проверяем режим при запуске
    ensure_hotspot_mode()

    # 5. Запускаем фоновый монитор
    start_monitor_thread()

    app.run(host=host, port=port, debug=True)