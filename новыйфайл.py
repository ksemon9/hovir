import hashlib
import uuid
import json
import os
import signal
import sys
import logging
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, session
from flask_socketio import SocketIO, emit, join_room, leave_room

# ------------------ НАСТРОЙКИ ------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(BASE_DIR, 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, 'data.json')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'supersecretkey')
socketio = SocketIO(app, async_mode='threading')

# ------------------ ЗАГРУЗКА / СОХРАНЕНИЕ ДАННЫХ ------------------
def load_data():
    if not os.path.exists(DATA_FILE):
        logger.info("Файл данных не найден, создаём новую базу")
        return {}, 1, [], {}
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    users = data.get('users', {})
    next_user_id = data.get('next_user_id', 1)
    rooms = data.get('rooms', [])
    raw_messages = data.get('messages', {})
    converted_messages = {}
    for room_id_str, msgs in raw_messages.items():
        room_id = int(room_id_str)
        if isinstance(msgs, list):
            converted_messages[room_id] = {1: msgs}
        elif isinstance(msgs, dict):
            converted_messages[room_id] = {int(sub_id): sub_msgs for sub_id, sub_msgs in msgs.items()}
        else:
            converted_messages[room_id] = {1: []}
    for room in rooms:
        room.setdefault('subrooms', [{'id': 1, 'name': 'общий'}])
        room.setdefault('roles', {})
        room.setdefault('members', [])
        if room.get('creator_id') and str(room['creator_id']) not in room['roles']:
            room['roles'][str(room['creator_id'])] = 'owner'
        if room.get('creator_id') and room['creator_id'] not in room['members']:
            room['members'].append(room['creator_id'])
    return users, next_user_id, rooms, converted_messages

def save_data():
    data = {
        'users': users,
        'next_user_id': next_user_id,
        'rooms': rooms,
        'messages': {str(k): {str(sk): sm for sk, sm in v.items()} for k, v in messages.items()}
    }
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.debug("Данные сохранены")

users, next_user_id, rooms, messages = load_data()

# ------------------ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ------------------
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_by_id(uid):
    for uname, udata in users.items():
        if udata['id'] == uid:
            return uname, udata
    return None, None

def get_user_role(room, user_id):
    if user_id == room.get('creator_id'):
        return 'owner'
    return room.get('roles', {}).get(str(user_id), 'member')

def can_manage_roles(room, user_id):
    return get_user_role(room, user_id) in ('owner', 'admin')

def can_create_subrooms(room, user_id):
    return can_manage_roles(room, user_id)

def can_delete_message(room, user_id):
    return get_user_role(room, user_id) in ('owner', 'admin', 'moderator')

def broadcast_status(user_id, status):
    username, _ = get_user_by_id(user_id)
    if not username:
        return
    for room in rooms:
        if room.get('type') == 'dm':
            if user_id in room.get('members', []):
                socketio.emit('user_status_update',
                              {'user_id': user_id, 'username': username, 'status': status},
                              to=str(room['id']))
        else:
            if user_id == room.get('creator_id') or user_id in room.get('members', []):
                socketio.emit('user_status_update',
                              {'user_id': user_id, 'username': username, 'status': status},
                              to=str(room['id']))

# ------------------ GRACEFUL SHUTDOWN ------------------
def shutdown_signal_handler(signum, frame):
    logger.info(f"Сигнал {signum}, сохраняем данные...")
    save_data()
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown_signal_handler)
signal.signal(signal.SIGINT, shutdown_signal_handler)

# ------------------ ЭЛЕГАНТНЫЕ ШАБЛОНЫ ВХОДА / РЕГИСТРАЦИИ ------------------
LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hovir | Вход</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', 'Inter', system-ui, sans-serif;
            min-height: 100vh;
            background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
            display: flex;
            justify-content: center;
            align-items: center;
            position: relative;
            overflow: hidden;
        }
        .particle {
            position: absolute;
            background: rgba(255,255,255,0.2);
            border-radius: 50%;
            pointer-events: none;
            animation: floatParticle linear infinite;
        }
        @keyframes floatParticle {
            0% { transform: translateY(0) rotate(0deg); opacity: 0; }
            50% { opacity: 0.6; }
            100% { transform: translateY(-100vh) rotate(720deg); opacity: 0; }
        }
        .glass-card {
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(12px);
            border-radius: 32px;
            padding: 2.5rem;
            width: 420px;
            max-width: 90%;
            box-shadow: 0 25px 45px rgba(0,0,0,0.3), 0 0 0 1px rgba(255,255,255,0.1);
            transition: transform 0.3s ease;
            z-index: 1;
        }
        .glass-card:hover { transform: translateY(-5px); }
        .logo { text-align: center; margin-bottom: 2rem; font-size: 3.5rem; animation: pulse 2s infinite; }
        @keyframes pulse { 0% { transform: scale(1); } 50% { transform: scale(1.05); } 100% { transform: scale(1); } }
        h2 { color: #fff; text-align: center; font-weight: 600; font-size: 1.8rem; margin-bottom: 1.8rem; letter-spacing: -0.5px; }
        .input-group { margin-bottom: 1.5rem; position: relative; }
        .input-group i { position: absolute; left: 16px; top: 50%; transform: translateY(-50%); font-style: normal; color: rgba(255,255,255,0.7); }
        input {
            width: 100%;
            padding: 14px 16px 14px 44px;
            background: rgba(255,255,255,0.15);
            border: 1px solid rgba(255,255,255,0.3);
            border-radius: 28px;
            color: white;
            font-size: 1rem;
            transition: 0.3s;
            outline: none;
        }
        input:focus { border-color: #a477ff; background: rgba(255,255,255,0.25); box-shadow: 0 0 15px rgba(164,119,255,0.3); }
        input::placeholder { color: rgba(255,255,255,0.6); }
        button {
            width: 100%;
            padding: 14px;
            background: linear-gradient(90deg, #a477ff, #6c5ce7);
            border: none;
            border-radius: 28px;
            color: white;
            font-size: 1.1rem;
            font-weight: 600;
            cursor: pointer;
            transition: 0.3s;
            margin-top: 0.5rem;
            box-shadow: 0 5px 15px rgba(108,92,231,0.3);
        }
        button:hover { transform: scale(1.02); background: linear-gradient(90deg, #b589ff, #7d6eff); box-shadow: 0 8px 20px rgba(108,92,231,0.5); }
        .link { text-align: center; margin-top: 1.8rem; color: rgba(255,255,255,0.8); }
        .link a { color: #d4bfff; text-decoration: none; font-weight: 500; }
        .link a:hover { color: white; text-decoration: underline; }
        .error-msg { background: rgba(255,80,80,0.2); border-left: 4px solid #ff5252; padding: 10px; border-radius: 12px; margin-bottom: 1rem; color: #ffb3b3; }
    </style>
</head>
<body>
    <div id="particles"></div>
    <div class="glass-card">
        <div class="logo">🌸</div>
        <h2>Добро пожаловать</h2>
        {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
        <form method="POST">
            <div class="input-group"><i>👤</i><input type="text" name="username" placeholder="Имя пользователя" required></div>
            <div class="input-group"><i>🔒</i><input type="password" name="password" placeholder="Пароль" required></div>
            <button type="submit">Войти</button>
        </form>
        <div class="link">Нет аккаунта? <a href="{{ url_for('register') }}">Создать</a></div>
    </div>
    <script>
        for(let i=0;i<80;i++) {
            let p = document.createElement('div');
            p.className = 'particle';
            let s = Math.random()*6+2;
            p.style.width = s+'px'; p.style.height = s+'px';
            p.style.left = Math.random()*100+'%';
            p.style.animationDuration = Math.random()*15+8+'s';
            p.style.animationDelay = Math.random()*10+'s';
            document.getElementById('particles').appendChild(p);
        }
    </script>
</body>
</html>
'''

REGISTER_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hovir | Регистрация</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', 'Inter', system-ui, sans-serif;
            min-height: 100vh;
            background: linear-gradient(135deg, #1a1a2e, #16213e, #0f3460);
            display: flex;
            justify-content: center;
            align-items: center;
            position: relative;
            overflow: hidden;
        }
        .orb {
            position: absolute;
            width: 300px;
            height: 300px;
            background: radial-gradient(circle, rgba(108,92,231,0.3), transparent);
            border-radius: 50%;
            filter: blur(60px);
            animation: moveOrb 12s infinite alternate;
        }
        @keyframes moveOrb {
            0% { transform: translate(-20%, -20%); }
            100% { transform: translate(20%, 20%); }
        }
        .glass-card {
            background: rgba(0,0,0,0.5);
            backdrop-filter: blur(16px);
            border-radius: 40px;
            padding: 2.8rem;
            width: 440px;
            max-width: 90%;
            box-shadow: 0 20px 40px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.1);
            transition: all 0.3s;
            z-index: 1;
        }
        .glass-card:hover { transform: scale(1.01); }
        .logo { text-align: center; font-size: 3rem; margin-bottom: 1rem; animation: bounce 2s infinite; }
        @keyframes bounce { 0%,100%{ transform: translateY(0); } 50%{ transform: translateY(-8px); } }
        h2 { text-align: center; color: #ffffff; font-weight: 700; font-size: 2rem; margin-bottom: 2rem; letter-spacing: -0.3px; }
        .input-group { margin-bottom: 1.5rem; position: relative; }
        .input-group i { position: absolute; left: 18px; top: 50%; transform: translateY(-50%); font-style: normal; font-size: 1.2rem; }
        input {
            width: 100%;
            padding: 14px 18px 14px 48px;
            background: rgba(255,255,255,0.1);
            border: 1.5px solid rgba(255,255,255,0.25);
            border-radius: 36px;
            color: white;
            font-size: 1rem;
            transition: 0.2s;
            outline: none;
        }
        input:focus { border-color: #f0a6ff; background: rgba(255,255,255,0.2); box-shadow: 0 0 12px rgba(240,166,255,0.3); }
        button {
            width: 100%;
            padding: 14px;
            background: linear-gradient(100deg, #f0a6ff, #a477ff);
            border: none;
            border-radius: 36px;
            font-size: 1.1rem;
            font-weight: bold;
            color: white;
            cursor: pointer;
            transition: 0.2s;
            margin-top: 0.5rem;
        }
        button:hover { transform: scale(1.02); background: linear-gradient(100deg, #ffb8ff, #b589ff); box-shadow: 0 6px 20px rgba(240,166,255,0.4); }
        .link { text-align: center; margin-top: 1.5rem; color: #dddddd; }
        .link a { color: #f0a6ff; text-decoration: none; font-weight: 500; }
        .link a:hover { text-decoration: underline; }
        .error-msg { background: rgba(255,80,80,0.2); border-left: 4px solid #ff5252; padding: 10px; border-radius: 12px; margin-bottom: 1rem; color: #ffb3b3; }
    </style>
</head>
<body>
    <div class="orb" style="top:10%; left:-10%;"></div>
    <div class="orb" style="bottom:5%; right:-5%; width:400px; height:400px; background:radial-gradient(circle, rgba(240,166,255,0.2), transparent);"></div>
    <div class="glass-card">
        <div class="logo">✨</div>
        <h2>Создать аккаунт</h2>
        {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
        <form method="POST">
            <div class="input-group"><i>👤</i><input type="text" name="username" placeholder="Имя пользователя" required></div>
            <div class="input-group"><i>🔐</i><input type="password" name="password" placeholder="Пароль" required></div>
            <button type="submit">Зарегистрироваться</button>
        </form>
        <div class="link">Уже есть аккаунт? <a href="{{ url_for('login') }}">Войти</a></div>
    </div>
</body>
</html>
'''

# ------------------ ШАБЛОН ЧАТА (ПОЛНЫЙ, СОВРЕМЕННЫЙ) ------------------
CHAT_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Hovir — Чат</title>
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --bg: #1e1e2f;
            --sidebar: #2a2a3b;
            --text: #e0e0e0;
            --msg-bg: #3a3a4e;
            --input-bg: #2f2f42;
            --border: #4a4a60;
            --primary: #a477ff;
            --primary-hover: #b589ff;
            --danger: #ff6b6b;
        }
        body.theme-light {
            --bg: #f5f5f7;
            --sidebar: #ffffff;
            --text: #1e1e2f;
            --msg-bg: #e9e9ef;
            --input-bg: #ffffff;
            --border: #ccc;
            --primary: #6c5ce7;
        }
        body.theme-flower {
            --bg: rgba(30,20,40,0.9);
            --sidebar: rgba(45,35,55,0.85);
            --text: #fff0e0;
            --msg-bg: rgba(255,255,255,0.2);
            --input-bg: rgba(255,255,255,0.15);
            --border: rgba(255,220,180,0.5);
            --primary: #ff9f4a;
        }
        body { font-family: 'Inter', 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; transition: all 0.3s; }
        .sidebar { width: 280px; background: var(--sidebar); border-right: 1px solid var(--border); display: flex; flex-direction: column; }
        .sidebar-header { padding: 16px; background: var(--primary); color: white; font-weight: 700; font-size: 1.2rem; display: flex; align-items: center; gap: 8px; }
        .sidebar-header::before { content: "🌼"; font-size: 1.6rem; animation: spin 8s linear infinite; }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        .view-switch { display: flex; gap: 4px; padding: 8px; border-bottom: 1px solid var(--border); }
        .view-switch button { flex:1; padding:8px; border:none; border-radius:6px; background:var(--input-bg); color:var(--text); cursor:pointer; }
        .view-switch button.active { background: var(--primary); color:white; }
        .room-list { list-style:none; flex:1; overflow-y:auto; padding:8px 0; }
        .room-item { padding:10px 16px; margin:2px 8px; border-radius:10px; cursor:pointer; transition:0.2s; display:flex; align-items:center; gap:10px; }
        .room-item:hover { background: var(--input-bg); transform:translateX(3px); }
        .room-item.active { background: var(--primary); color:white; font-weight:600; }
        .sidebar-buttons { padding:8px; border-top:1px solid var(--border); display:flex; gap:4px; }
        .sidebar-buttons button { flex:1; background: var(--primary); border:none; color:white; padding:8px; border-radius:8px; cursor:pointer; }
        .user-info { padding:12px 16px; border-top:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; }
        .user-name { display:flex; align-items:center; gap:6px; }
        .user-avatar { width:32px; height:32px; border-radius:50%; object-fit:cover; background: var(--primary); }
        .status-select { background: var(--input-bg); color: var(--text); border:1px solid var(--border); border-radius:12px; padding:4px 8px; font-size:0.75rem; }
        .chat-area { flex:1; display:flex; flex-direction:column; height:100%; overflow:hidden; }
        .chat-header { padding:12px 20px; background: var(--sidebar); border-bottom:1px solid var(--border); display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
        .subroom-selector { background: var(--input-bg); border:1px solid var(--border); border-radius:20px; padding:6px 12px; color:var(--text); }
        .chat-content { flex:1; overflow-y:auto; padding:16px; }
        .messages { display:flex; flex-direction:column; gap:12px; }
        .message { max-width:75%; padding:10px 14px; border-radius:18px; background: var(--msg-bg); align-self:flex-start; position:relative; animation: slideDown 0.2s ease; }
        @keyframes slideDown { from { opacity:0; transform:translateY(-10px); } to { opacity:1; transform:translateY(0); } }
        .message-header { display:flex; align-items:center; gap:8px; font-size:0.85rem; }
        .message-user { font-weight:700; color: var(--primary); cursor:pointer; display:flex; align-items:center; gap:5px; }
        .message-time { color:#81c784; font-size:0.75rem; }
        .message-image { max-width:100%; max-height:300px; border-radius:12px; margin-top:6px; cursor:pointer; }
        .delete-msg { position:absolute; right:8px; top:8px; background:none; border:none; color:var(--danger); cursor:pointer; opacity:0; transition:0.2s; }
        .message:hover .delete-msg { opacity:1; }
        .input-area { padding:12px 20px; background: var(--sidebar); border-top:1px solid var(--border); display:flex; gap:10px; }
        .input-area textarea { flex:1; padding:12px 16px; border:2px solid var(--border); border-radius:24px; background: var(--input-bg); color:var(--text); resize:none; max-height:120px; }
        .input-area button { background: var(--primary); border:none; padding:0 20px; border-radius:24px; color:white; font-weight:600; cursor:pointer; }
        .file-input-label { background: var(--primary); padding:0 16px; border-radius:24px; display:inline-flex; align-items:center; gap:6px; cursor:pointer; }
        .file-input-label input { display:none; }
        .welcome-screen { flex:1; display:flex; flex-direction:column; justify-content:center; align-items:center; text-align:center; background: linear-gradient(135deg, rgba(164,119,255,0.1), rgba(108,92,231,0.1)); }
        .welcome-logo { font-size:5rem; animation: pulse 1.2s infinite alternate, spin 6s linear infinite; }
        .features-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:1rem; max-width:700px; margin-top:2rem; }
        .feature-card { background: var(--sidebar); padding:1rem; border-radius:16px; border:1px solid var(--border); }
        .modal { display:none; position:fixed; z-index:1000; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.7); backdrop-filter:blur(4px); }
        .modal-content { background: var(--sidebar); margin:5% auto; padding:24px; border-radius:20px; width:500px; max-width:90%; position:relative; }
        .close-modal { position:absolute; right:20px; top:16px; font-size:28px; cursor:pointer; }
        .btn-primary { background: var(--primary); color:white; border:none; padding:10px 24px; border-radius:24px; cursor:pointer; }
        .btn-cancel { background:#aaa; color:white; border:none; padding:10px 24px; border-radius:24px; cursor:pointer; }
        .modal-actions { display:flex; gap:12px; margin-top:24px; justify-content:flex-end; }
        .status-badge { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }
        .status-online { background-color:#2ecc71; }
        .status-offline { background-color:#7f8c8d; }
        .loading-indicator { text-align:center; padding:10px; color: var(--primary); display:none; }
    </style>
</head>
<body class="theme-{{ user_theme }}">
    <div class="sidebar">
        <div class="sidebar-header">Hovir Мессенджер</div>
        <div class="view-switch">
            <button id="btn-rooms" class="active" onclick="switchView('rooms')">Каналы</button>
            <button id="btn-dm" onclick="switchView('dm')">ЛС</button>
        </div>
        <ul class="room-list" id="room-list"></ul>
        <div class="sidebar-buttons" id="sidebar-buttons-rooms">
            <button onclick="openCreateModal('channel')">📢 Канал</button>
            <button onclick="openCreateModal('group')">👥 Группа</button>
            <button onclick="openSearchModal()">🔍 Найти</button>
        </div>
        <div class="sidebar-buttons" id="sidebar-buttons-dm" style="display:none;">
            <button onclick="openUserSearchModal()">✉️ Новое ЛС</button>
            <button onclick="switchView('rooms')">↩️ К каналам</button>
        </div>
        <div class="user-info">
            <div class="user-name">
                <img id="sidebar-avatar" class="user-avatar" src="{{ user_avatar }}" onerror="this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22%3E%3Ccircle cx=%2250%22 cy=%2250%22 r=%2250%22 fill=%22%23a477ff%22/%3E%3Ctext x=%2250%22 y=%2267%22 text-anchor=%22middle%22 fill=%22white%22 font-size=%2245%22%3E{{ username[0]|upper }}%3C/text%3E%3C/svg%3E'">
                <span>{{ username }}</span>
                <select id="status-select" class="status-select" onchange="changeStatus(this.value)">
                    <option value="online">🟢 Онлайн</option><option value="away">🌙 Отошёл</option>
                    <option value="dnd">⛔ Не беспокоить</option><option value="offline">⚫ Не в сети</option>
                </select>
            </div>
            <span class="settings-btn" onclick="openProfileSettings()">⚙️</span>
            <a href="{{ url_for('logout') }}" class="logout">🚪</a>
        </div>
    </div>
    <div class="chat-area">
        <div class="chat-header">
            <span id="room-title">Hovir</span>
            <select id="subroom-selector" class="subroom-selector" style="display:none;" onchange="changeSubroom()"></select>
            <span id="room-settings-btn" style="display:none;" onclick="openRoomSettings()">⚙️</span>
            <span id="manage-roles-btn" style="display:none;" onclick="openManageRoles()">👥 Роли</span>
            <span id="create-subroom-btn" style="display:none;" onclick="openCreateSubroom()">➕ Подканал</span>
        </div>
        <div id="welcome-screen" class="welcome-screen">
            <div class="welcome-logo">🌼</div>
            <h1>Добро пожаловать в Hovir</h1>
            <div class="features-grid">
                <div class="feature-card">📢 Каналы и группы</div>
                <div class="feature-card">🔐 Подканалы с правами</div>
                <div class="feature-card">👑 Роли: Админ, Модератор</div>
                <div class="feature-card">💬 Личные сообщения</div>
                <div class="feature-card">🖼️ Отправка фото</div>
            </div>
        </div>
        <div id="chat-interface" style="display:none; flex-direction:column; flex:1; min-height:0;">
            <div class="chat-content" id="messages-container">
                <div class="messages" id="messages"></div>
                <div id="loading-older" class="loading-indicator">⏳ Загрузка старых сообщений...</div>
            </div>
            <div class="input-area">
                <textarea id="message-input" placeholder="Введите сообщение..." rows="1" disabled></textarea>
                <label class="file-input-label">📷 Фото<input type="file" id="image-input" accept="image/jpeg,image/png,image/gif" disabled></label>
                <button id="send-btn" disabled>➤ Отправить</button>
            </div>
        </div>
    </div>
    <!-- Модальные окна -->
    <div id="create-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('create-modal')">&times;</span><h2>Создать</h2><label>Название</label><input id="create-name"><label>Описание</label><textarea id="create-desc" rows="2"></textarea><label>Тип</label><select id="create-type"><option value="channel">Канал</option><option value="group">Группа</option></select><label><input type="checkbox" id="create-private"> Приватный</label><label>Ссылка-приглашение</label><input id="create-link"><div class="modal-actions"><button class="btn-cancel" onclick="closeModal('create-modal')">Отмена</button><button class="btn-primary" onclick="submitCreateRoom()">Создать</button></div></div></div>
    <div id="search-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('search-modal')">&times;</span><h2>Поиск каналов</h2><input id="search-query" placeholder="Название или ссылка"><button class="btn-primary" onclick="searchRooms()">Искать</button><div id="search-results"></div></div></div>
    <div id="user-search-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('user-search-modal')">&times;</span><h2>Поиск пользователей</h2><input id="user-search-query"><button class="btn-primary" onclick="searchUsers()">Искать</button><div id="user-search-results"></div></div></div>
    <div id="room-settings-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('room-settings-modal')">&times;</span><h2>Настройки комнаты</h2><label>Название</label><input id="rs-name"><label>Описание</label><textarea id="rs-desc" rows="2"></textarea><label>Приватность</label><select id="rs-private"><option value="0">Публичная</option><option value="1">Приватная</option></select><label>Ссылка</label><input id="rs-link"><div class="modal-actions"><button class="btn-cancel" onclick="closeModal('room-settings-modal')">Отмена</button><button class="btn-primary" onclick="submitRoomSettings()">Сохранить</button></div></div></div>
    <div id="settings-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('settings-modal')">&times;</span><h2>Настройки</h2><div class="settings-tabs"><button class="settings-tab active" onclick="switchSettingsTab('profile')">👤 Профиль</button><button class="settings-tab" onclick="switchSettingsTab('appearance')">🎨 Внешний вид</button><button class="settings-tab" onclick="switchSettingsTab('help')">❓ Справка</button><button class="settings-tab" onclick="switchSettingsTab('bug')">🐛 Ошибка</button></div><div id="profile-tab" class="tab-content active"><div class="avatar-upload"><img id="settings-avatar-preview" class="avatar-preview" src="{{ user_avatar }}" onclick="document.getElementById('settings-avatar-input').click()" style="width:100px;height:100px;border-radius:50%;cursor:pointer;"><input type="file" id="settings-avatar-input" accept="image/jpeg,image/png,image/gif" style="display:none"></div><label>О себе</label><textarea id="profile-bio" rows="3">{{ user_bio }}</textarea></div><div id="appearance-tab" class="tab-content"><label>Тема</label><select id="profile-theme"><option value="dark">🌙 Тёмная</option><option value="light">☀️ Светлая</option><option value="flower">🌸 Цветочная</option></select><label>Звук уведомлений</label><select id="notification-sound"><option value="on">Вкл</option><option value="off">Выкл</option></select></div><div id="help-tab" class="tab-content"><p>📌 Основные функции: Каналы, подканалы, роли, ЛС, фото.</p></div><div id="bug-tab" class="tab-content"><label>Тема</label><input id="bug-subject"><label>Описание</label><textarea id="bug-description" rows="3"></textarea></div><div class="modal-actions"><button class="btn-cancel" onclick="closeModal('settings-modal')">Закрыть</button><button class="btn-primary" onclick="saveAllSettings()">Сохранить</button></div></div></div>
    <div id="user-profile-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('user-profile-modal')">&times;</span><h2>Профиль пользователя</h2><div id="user-profile-content"></div></div></div>
    <div id="manage-roles-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('manage-roles-modal')">&times;</span><h2>Управление ролями</h2><div id="roles-list"></div></div></div>
    <div id="create-subroom-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('create-subroom-modal')">&times;</span><h2>Создать подканал</h2><label>Название</label><input id="subroom-name"><div class="modal-actions"><button class="btn-cancel" onclick="closeModal('create-subroom-modal')">Отмена</button><button class="btn-primary" onclick="submitCreateSubroom()">Создать</button></div></div></div>
    <div id="floating-container" class="floating-container" style="display:none;"></div>
    <script>
        const socket = io();
        let currentRoomId = null, currentSubroomId = null, currentRoomSettings = null, currentView = 'rooms';
        const username = "{{ username }}", userId = {{ user_id }};
        let userStatuses = {}, currentDmPartner = null, currentSubrooms = [], pendingAvatarBase64 = null;
        let oldestMessageId = null, hasMoreMessages = true, loadingOlder = false, isWindowFocused = true;
        let unreadTotal = 0, unreadPerRoom = {};

        function updateTitle() { document.title = unreadTotal>0 ? `📩 (${unreadTotal}) Hovir — Чат` : `Hovir — Чат`; }
        function resetUnreadForRoom(roomId) { if(unreadPerRoom[roomId]) { unreadTotal-=unreadPerRoom[roomId]; delete unreadPerRoom[roomId]; if(unreadTotal<0) unreadTotal=0; updateTitle(); } }
        function addUnreadForRoom(roomId,count=1) { if(!unreadPerRoom[roomId]) unreadPerRoom[roomId]=0; unreadPerRoom[roomId]+=count; unreadTotal+=count; updateTitle(); if(currentRoomId===roomId) resetUnreadForRoom(roomId); }
        function playNotificationSound() { if(localStorage.getItem('notificationSound')!=='off') try{let a=new AudioContext(),o=a.createOscillator(),g=a.createGain();o.connect(g);g.connect(a.destination);o.frequency.value=800;g.gain.value=0.2;o.start();g.gain.exponentialRampToValueAtTime(0.00001,a.currentTime+0.3);o.stop(a.currentTime+0.3);a.resume();}catch(e){} }
        window.addEventListener('focus',()=>{isWindowFocused=true;}); window.addEventListener('blur',()=>{isWindowFocused=false;});
        const roomList=document.getElementById('room-list'), messagesDiv=document.getElementById('messages'), roomTitle=document.getElementById('room-title');
        const roomSettingsBtn=document.getElementById('room-settings-btn'), manageRolesBtn=document.getElementById('manage-roles-btn'), createSubroomBtn=document.getElementById('create-subroom-btn');
        const subroomSelector=document.getElementById('subroom-selector'), messageTextarea=document.getElementById('message-input'), sendBtn=document.getElementById('send-btn');
        const welcomeScreen=document.getElementById('welcome-screen'), chatInterface=document.getElementById('chat-interface'), imageInput=document.getElementById('image-input');
        const messagesContainer=document.getElementById('messages-container'), loadingIndicator=document.getElementById('loading-older');
        function scrollToBottom(){ if(messagesContainer) setTimeout(()=>messagesContainer.scrollTop=messagesContainer.scrollHeight,20); }
        function autoResizeTextarea(){ if(messageTextarea){ messageTextarea.style.height='auto'; messageTextarea.style.height=Math.min(messageTextarea.scrollHeight,120)+'px'; } }
        function loadOlderMessages(){ if(loadingOlder||!hasMoreMessages||!currentRoomId||currentSubroomId===null||!oldestMessageId) return; loadingOlder=true; loadingIndicator.style.display='block'; socket.emit('load_older_messages',{room_id:currentRoomId,subroom_id:currentSubroomId,before_message_id:oldestMessageId}); }
        function onChatScroll(){ if(messagesContainer&&messagesContainer.scrollTop<=50&&!loadingOlder&&hasMoreMessages) loadOlderMessages(); }
        function applyTheme(theme){ document.body.className='theme-'+theme; }
        function getStatusIcon(status){ const cls={online:'status-online',offline:'status-offline',away:'status-away',dnd:'status-dnd'}[status]||'status-offline'; return `<span class="status-badge ${cls}"></span>`; }
        function changeStatus(newStatus){ socket.emit('set_status',{status:newStatus}); userStatuses[username]=newStatus; }
        function switchView(view){ currentView=view; document.getElementById('btn-rooms').classList.toggle('active',view==='rooms'); document.getElementById('btn-dm').classList.toggle('active',view==='dm'); document.getElementById('sidebar-buttons-rooms').style.display=view==='rooms'?'flex':'none'; document.getElementById('sidebar-buttons-dm').style.display=view==='dm'?'flex':'none'; if(currentRoomId){ socket.emit('leave',{room_id:currentRoomId}); currentRoomId=null; currentSubroomId=null; } welcomeScreen.style.display='flex'; chatInterface.style.display='none'; roomTitle.textContent='Hovir'; roomSettingsBtn.style.display='none'; manageRolesBtn.style.display='none'; createSubroomBtn.style.display='none'; subroomSelector.style.display='none'; messagesDiv.innerHTML=''; if(view==='rooms') socket.emit('request_room_list'); else if(view==='dm') socket.emit('get_dm_rooms'); }
        function updateList(rooms){ roomList.innerHTML=''; rooms.forEach(room=>{ const li=document.createElement('li'); li.className='room-item'; li.dataset.roomId=room.id; let icon=''; if(room.type==='group')icon=' 👥'; else if(room.type==='channel')icon=' 📢'; else if(room.type==='dm')icon=' ✉️'; if(room.type==='dm'){ const names=room.name.split(' & '); const partner=names[0]===username?names[1]:names[0]; const statusHtml=getStatusIcon(userStatuses[partner]||'offline'); li.innerHTML=`<span class="dm-status-badge">${statusHtml}</span>${room.name}${icon}`; }else li.textContent=room.name+icon; roomList.appendChild(li); }); document.querySelectorAll('.room-item').forEach(item=>{ item.addEventListener('click',()=>{ document.querySelectorAll('.room-item').forEach(r=>r.classList.remove('active')); item.classList.add('active'); joinRoom(parseInt(item.dataset.roomId)); }); }); }
        function joinRoom(roomId){ if(currentRoomId) socket.emit('leave',{room_id:currentRoomId}); currentRoomId=roomId; resetUnreadForRoom(roomId); welcomeScreen.style.display='none'; chatInterface.style.display='flex'; messageTextarea.disabled=false; sendBtn.disabled=false; imageInput.disabled=false; messagesDiv.innerHTML=''; oldestMessageId=null; hasMoreMessages=true; loadingOlder=false; if(messagesContainer){ messagesContainer.removeEventListener('scroll',onChatScroll); messagesContainer.addEventListener('scroll',onChatScroll); } socket.emit('join',{room_id:roomId}); }
        socket.on('room_info',(data)=>{ currentRoomSettings=data; currentSubrooms=data.subrooms||[{id:1,name:'общий'}]; roomTitle.textContent=data.name; if(data.type!=='dm'){ const myRole=data.user_role; manageRolesBtn.style.display=(myRole==='owner'||myRole==='admin')?'inline':'none'; createSubroomBtn.style.display=(myRole==='owner'||myRole==='admin')?'inline':'none'; roomSettingsBtn.style.display=(myRole==='owner')?'inline':'none'; subroomSelector.innerHTML=''; currentSubrooms.forEach(sr=>{ let opt=document.createElement('option'); opt.value=sr.id; opt.textContent=sr.name; subroomSelector.appendChild(opt); }); subroomSelector.style.display='inline-block'; if(currentSubroomId===null&&currentSubrooms.length) currentSubroomId=currentSubrooms[0].id; subroomSelector.value=currentSubroomId; loadMessagesForSubroom(); }else{ const names=data.name.split(' & '); currentDmPartner=names[0]===username?names[1]:names[0]; currentSubroomId=1; loadMessagesForSubroom(); manageRolesBtn.style.display='none'; createSubroomBtn.style.display='none'; subroomSelector.style.display='none'; } });
        function loadMessagesForSubroom(){ if(!currentRoomId||currentSubroomId===null) return; oldestMessageId=null; hasMoreMessages=true; loadingOlder=false; messagesDiv.innerHTML=''; socket.emit('load_subroom_messages',{room_id:currentRoomId,subroom_id:currentSubroomId}); }
        socket.on('subroom_history',(history)=>{ messagesDiv.innerHTML=''; if(history.length){ history.forEach(msg=>addMessage(msg,false)); oldestMessageId=history[0].message_id; hasMoreMessages=(history.length===50); }else hasMoreMessages=false; scrollToBottom(); });
        socket.on('older_messages',(olderMsgs)=>{ if(!olderMsgs||!olderMsgs.length){ hasMoreMessages=false; loadingIndicator.style.display='none'; loadingOlder=false; return; } const oldH=messagesContainer.scrollHeight, oldT=messagesContainer.scrollTop; olderMsgs.forEach(msg=>addMessage(msg,true)); oldestMessageId=olderMsgs[0].message_id; messagesContainer.scrollTop=oldT+(messagesContainer.scrollHeight-oldH); hasMoreMessages=(olderMsgs.length===50); loadingIndicator.style.display='none'; loadingOlder=false; });
        socket.on('new_message',(data)=>{ if(data.username!==username){ if(currentRoomId!==data.room_id) addUnreadForRoom(data.room_id,1); if(!isWindowFocused) playNotificationSound(); } if(data.subroom_id===currentSubroomId&&currentRoomId===data.room_id){ addMessage(data,false); scrollToBottom(); if(!oldestMessageId) oldestMessageId=data.message_id; } });
        function addMessage(msg,prepend){ const div=document.createElement('div'); div.className='message'; div.dataset.msgId=msg.message_id; const statusIcon=getStatusIcon(userStatuses[msg.username]||'offline'); let delBtn=''; if(currentRoomSettings&&(currentRoomSettings.user_role==='owner'||currentRoomSettings.user_role==='admin'||currentRoomSettings.user_role==='moderator')) delBtn=`<button class="delete-msg" onclick="deleteMessage(${msg.message_id})">🗑️</button>`; let contentHtml=msg.is_image?`<img src="${escapeHtml(msg.content)}" class="message-image" onclick="window.open(this.src)">`:`<div class="message-text">${escapeHtml(msg.content)}</div>`; div.innerHTML=`<div class="message-header"><span class="message-user" onclick="openUserProfile('${escapeHtml(msg.username)}')">${statusIcon}${escapeHtml(msg.username)}</span><span class="message-time">${msg.timestamp}</span></div>${contentHtml}${delBtn}`; if(prepend) messagesDiv.insertBefore(div,messagesDiv.firstChild); else messagesDiv.appendChild(div); }
        function deleteMessage(msgId){ if(confirm('Удалить сообщение?')) socket.emit('delete_message',{room_id:currentRoomId,subroom_id:currentSubroomId,message_id:msgId}); }
        socket.on('message_deleted',(data)=>{ if(data.subroom_id===currentSubroomId) document.querySelector(`.message[data-msg-id="${data.message_id}"]`)?.remove(); });
        function changeSubroom(){ currentSubroomId=parseInt(subroomSelector.value); loadMessagesForSubroom(); }
        function sendMessage(){ const content=messageTextarea.value.trim(); if(content&&currentRoomId&&currentSubroomId!==null){ socket.emit('send_message',{room_id:currentRoomId,subroom_id:currentSubroomId,message:content,is_image:false}); messageTextarea.value=''; autoResizeTextarea(); messageTextarea.focus(); } }
        function sendImage(base64){ if(base64&&currentRoomId&&currentSubroomId!==null) socket.emit('send_message',{room_id:currentRoomId,subroom_id:currentSubroomId,message:base64,is_image:true}); }
        imageInput.addEventListener('change',(e)=>{ const file=e.target.files[0]; if(!file) return; if(file.size>5*1024*1024){ alert('Файл слишком большой (макс. 5 МБ)'); imageInput.value=''; return; } const reader=new FileReader(); reader.onload=(ev)=>{ sendImage(ev.target.result); imageInput.value=''; }; reader.readAsDataURL(file); });
        messageTextarea.addEventListener('keypress',(e)=>{ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); sendMessage(); } });
        messageTextarea.addEventListener('input',autoResizeTextarea);
        function openManageRoles(){ socket.emit('get_room_members',{room_id:currentRoomId}); }
        socket.on('room_members',(members)=>{ const cont=document.getElementById('roles-list'); cont.innerHTML=''; members.forEach(m=>{ const div=document.createElement('div'); div.className='user-list-item'; div.innerHTML=`<span><strong>${escapeHtml(m.username)}</strong> <span class="role-badge">${m.role}</span></span>`; if((currentRoomSettings.user_role==='owner'||currentRoomSettings.user_role==='admin')&&m.user_id!==currentRoomSettings.creator_id){ const sel=document.createElement('select'); sel.innerHTML=`<option value="member" ${m.role==='member'?'selected':''}>Пользователь</option><option value="moderator" ${m.role==='moderator'?'selected':''}>Модератор</option><option value="admin" ${m.role==='admin'?'selected':''}>Администратор</option>`; sel.onchange=()=>socket.emit('update_user_role',{room_id:currentRoomId,target_user_id:m.user_id,new_role:sel.value}); div.appendChild(sel); } cont.appendChild(div); }); document.getElementById('manage-roles-modal').style.display='block'; });
        function openCreateSubroom(){ document.getElementById('subroom-name').value=''; document.getElementById('create-subroom-modal').style.display='block'; }
        function submitCreateSubroom(){ const name=document.getElementById('subroom-name').value.trim(); if(!name) return alert('Введите название'); socket.emit('create_subroom',{room_id:currentRoomId,name:name}); closeModal('create-subroom-modal'); }
        socket.on('subroom_created',(subroom)=>{ currentSubrooms.push(subroom); const opt=document.createElement('option'); opt.value=subroom.id; opt.textContent=subroom.name; subroomSelector.appendChild(opt); subroomSelector.value=subroom.id; currentSubroomId=subroom.id; loadMessagesForSubroom(); });
        function escapeHtml(text){ const div=document.createElement('div'); div.textContent=text; return div.innerHTML; }
        function openCreateModal(type){ const modalTitle=document.getElementById('create-modal-title'), typeSelect=document.getElementById('create-type'); if(type==='channel'){ modalTitle.textContent='Создать канал'; typeSelect.value='channel'; }else if(type==='group'){ modalTitle.textContent='Создать группу'; typeSelect.value='group'; } document.getElementById('create-name').value=''; document.getElementById('create-desc').value=''; document.getElementById('create-private').checked=false; document.getElementById('create-link').value=''; document.getElementById('create-modal').style.display='block'; }
        function submitCreateRoom(){ const name=document.getElementById('create-name').value.trim(); if(!name) return alert('Введите название'); socket.emit('create_room',{name, description:document.getElementById('create-desc').value.trim(), type:document.getElementById('create-type').value, is_private:document.getElementById('create-private').checked, invite_link:document.getElementById('create-link').value.trim()}); closeModal('create-modal'); }
        function openSearchModal(){ document.getElementById('search-modal').style.display='block'; }
        function searchRooms(){ const q=document.getElementById('search-query').value.trim(); if(q) socket.emit('search_rooms',{query:q}); }
        socket.on('search_results',(res)=>{ const cont=document.getElementById('search-results'); if(!res.length){ cont.innerHTML='<p>Ничего не найдено</p>'; return; } cont.innerHTML=res.map(r=>`<div><span>${escapeHtml(r.name)} (${r.type})</span><button class="btn-primary" onclick="joinByInvite('${escapeHtml(r.invite_link)}')">Войти</button></div>`).join(''); });
        function joinByInvite(link){ socket.emit('join_by_invite',{invite_link:link}); closeModal('search-modal'); }
        function openUserSearchModal(){ document.getElementById('user-search-modal').style.display='block'; }
        function searchUsers(){ const q=document.getElementById('user-search-query').value.trim(); if(q) socket.emit('search_users',{query:q}); }
        socket.on('user_search_results',(users)=>{ const cont=document.getElementById('user-search-results'); if(!users.length){ cont.innerHTML='<p>Ничего не найдено</p>'; return; } cont.innerHTML=users.map(u=>`<div><span>👤 ${escapeHtml(u.username)}</span><button class="btn-primary" onclick="startDM(${u.id})">Написать</button></div>`).join(''); });
        function startDM(targetId){ socket.emit('create_dm',{target_user_id:targetId}); closeModal('user-search-modal'); }
        socket.on('dm_created',(room)=>{ if(currentView!=='dm') switchView('dm'); setTimeout(()=>document.querySelector(`.room-item[data-room-id="${room.id}"]`)?.click(),100); });
        function openRoomSettings(){ if(!currentRoomSettings) return; document.getElementById('rs-name').value=currentRoomSettings.name||''; document.getElementById('rs-desc').value=currentRoomSettings.description||''; document.getElementById('rs-private').value=currentRoomSettings.is_private?'1':'0'; document.getElementById('rs-link').value=currentRoomSettings.invite_link||''; document.getElementById('room-settings-modal').style.display='block'; }
        function submitRoomSettings(){ if(!currentRoomId) return; socket.emit('update_room',{room_id:currentRoomId,name:document.getElementById('rs-name').value.trim(),description:document.getElementById('rs-desc').value.trim(),is_private:document.getElementById('rs-private').value==='1',invite_link:document.getElementById('rs-link').value.trim()}); closeModal('room-settings-modal'); }
        function openProfileSettings(){ document.getElementById('settings-modal').style.display='block'; document.getElementById('profile-bio').value=document.getElementById('profile-bio').value||"{{ user_bio }}"; const preview=document.getElementById('settings-avatar-preview'); if(preview.src&&!preview.src.includes('data:image')&&preview.src!==window.location.href) preview.style.display='block'; else{ preview.src="{{ user_avatar }}"; if(preview.src&&preview.src!==window.location.href) preview.style.display='block'; else preview.style.display='none'; } pendingAvatarBase64=null; loadSoundSetting(); }
        function loadSoundSetting(){ const saved=localStorage.getItem('notificationSound'); if(saved==='on'||saved==='off') document.getElementById('notification-sound').value=saved; else{ document.getElementById('notification-sound').value='on'; localStorage.setItem('notificationSound','on'); } }
        document.getElementById('settings-avatar-input').addEventListener('change',function(e){ const file=e.target.files[0]; if(!file) return; if(file.size>2*1024*1024){ alert('Аватар не должен превышать 2 МБ'); this.value=''; return; } if(!file.type.match('image/jpeg|image/png|image/gif')){ alert('Только JPG, PNG или GIF'); this.value=''; return; } const reader=new FileReader(); reader.onload=function(ev){ const preview=document.getElementById('settings-avatar-preview'); preview.src=ev.target.result; preview.style.display='block'; pendingAvatarBase64=ev.target.result; }; reader.readAsDataURL(file); });
        function switchSettingsTab(tab){ document.querySelectorAll('.settings-tab').forEach(t=>t.classList.remove('active')); document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active')); if(tab==='profile'){ document.querySelector('.settings-tab:nth-child(1)').classList.add('active'); document.getElementById('profile-tab').classList.add('active'); }else if(tab==='appearance'){ document.querySelector('.settings-tab:nth-child(2)').classList.add('active'); document.getElementById('appearance-tab').classList.add('active'); }else if(tab==='help'){ document.querySelector('.settings-tab:nth-child(3)').classList.add('active'); document.getElementById('help-tab').classList.add('active'); }else if(tab==='bug'){ document.querySelector('.settings-tab:nth-child(4)').classList.add('active'); document.getElementById('bug-tab').classList.add('active'); } }
        function saveAllSettings(){ const newTheme=document.getElementById('profile-theme').value; const bio=document.getElementById('profile-bio').value.trim(); const soundValue=document.getElementById('notification-sound').value; localStorage.setItem('notificationSound',soundValue); socket.emit('update_profile',{avatar:pendingAvatarBase64,bio:bio,theme:newTheme}); applyTheme(newTheme); if(pendingAvatarBase64) document.getElementById('sidebar-avatar').src=pendingAvatarBase64; closeModal('settings-modal'); }
        function submitBug(){ const subject=document.getElementById('bug-subject').value.trim(), description=document.getElementById('bug-description').value.trim(), email=document.getElementById('bug-email').value.trim(); if(!subject||!description){ alert('Заполните тему и описание'); return; } const bugReport=`Тема: ${subject}\\nОписание: ${description}\\nEmail: ${email||'не указан'}\\nПользователь: ${username}\\nВремя: ${new Date().toLocaleString()}`; socket.emit('report_bug',{report:bugReport}); alert('Спасибо! Отправлено разработчику.'); document.getElementById('bug-subject').value=''; document.getElementById('bug-description').value=''; document.getElementById('bug-email').value=''; }
        function openUserProfile(targetUsername){ socket.emit('get_user_profile',{username:targetUsername}); document.getElementById('user-profile-modal').style.display='block'; }
        socket.on('user_profile',(data)=>{ const cont=document.getElementById('user-profile-content'); if(data.error){ cont.innerHTML=`<p style="color:red;">${escapeHtml(data.error)}</p>`; return; } const statusText={online:'🟢 Онлайн',away:'🌙 Отошёл',dnd:'⛔ Не беспокоить',offline:'⚫ Не в сети'}[userStatuses[data.username]||'offline']; cont.innerHTML=`<p><strong>Имя:</strong> ${escapeHtml(data.username)}</p><p><strong>Статус:</strong> ${statusText}</p><p><strong>О себе:</strong> ${escapeHtml(data.bio||'не указано')}</p>${data.avatar?`<img src="${escapeHtml(data.avatar)}" style="max-width:100px;border-radius:50%;">`:''}`; });
        socket.on('bug_received',()=>{});
        function closeModal(id){ document.getElementById(id).style.display='none'; }
        window.onclick=function(e){ if(e.target.classList.contains('modal')) e.target.style.display='none'; };
        sendBtn.addEventListener('click',sendMessage);
        socket.on('connect',()=>socket.emit('request_all_statuses'));
        socket.on('all_statuses',(statusMap)=>{ userStatuses=statusMap; document.getElementById('status-select').value=userStatuses[username]||'online'; });
        socket.on('user_status_update',(data)=>{ userStatuses[data.username]=data.status; if(currentRoomSettings?.type==='dm'&&currentDmPartner===data.username) roomTitle.innerHTML=roomTitle.textContent.split(' — ')[0]; });
        socket.on('room_list_update',(rooms)=>{ if(currentView==='rooms') updateList(rooms); });
        socket.on('dm_rooms_update',(rooms)=>{ if(currentView==='dm') updateList(rooms); });
        socket.on('room_updated',()=>{ if(currentView==='rooms') socket.emit('request_room_list'); });
        socket.on('avatar_update',(data)=>{ if(data.username===username) document.getElementById('sidebar-avatar').src=data.avatar; });
        const initialTheme="{{ user_theme }}"; applyTheme(initialTheme); switchView('rooms');
    </script>
</body>
</html>
'''

# ------------------ FLASK МАРШРУТЫ ------------------
@app.route('/')
def index():
    return redirect(url_for('chat') if 'user_id' in session else url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users and users[username]['password_hash'] == hash_password(password):
            session['user_id'] = users[username]['id']
            session['username'] = username
            session['theme'] = users[username].get('theme', 'dark')
            return redirect(url_for('chat'))
        return render_template_string(LOGIN_TEMPLATE, error="Неверное имя или пароль")
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/register', methods=['GET', 'POST'])
def register():
    global next_user_id
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users:
            return render_template_string(REGISTER_TEMPLATE, error="Пользователь уже существует")
        users[username] = {
            'password_hash': hash_password(password),
            'id': next_user_id,
            'avatar': '',
            'theme': 'dark',
            'bio': '',
            'status': 'offline'
        }
        next_user_id += 1
        save_data()
        return redirect(url_for('login'))
    return render_template_string(REGISTER_TEMPLATE)

@app.route('/chat')
def chat():
    if 'user_id' not in session or 'username' not in session:
        return redirect(url_for('login'))
    user = users.get(session['username'])
    if not user:
        session.clear()
        return redirect(url_for('login'))
    my_rooms = [r for r in rooms if r.get('type') != 'dm' and (session['user_id'] == r.get('creator_id') or session['user_id'] in r.get('members', []))]
    return render_template_string(
        CHAT_TEMPLATE,
        rooms=my_rooms,
        username=session['username'],
        user_id=user['id'],
        user_theme=user.get('theme', 'dark'),
        user_avatar=user.get('avatar', ''),
        user_bio=user.get('bio', '')
    )

@app.route('/logout')
def logout():
    uid = session.get('user_id')
    if uid:
        username = session.get('username')
        if username in users:
            users[username]['status'] = 'offline'
            broadcast_status(uid, 'offline')
            save_data()
    session.clear()
    return redirect(url_for('login'))

@app.route('/invite/<invite_link>')
def invite(invite_link):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    room = next((r for r in rooms if r.get('invite_link') == invite_link), None)
    if not room:
        return "Комната не найдена", 404
    if room.get('type') == 'dm':
        return "Нельзя войти в ЛС по ссылке", 403
    uid = session['user_id']
    if uid not in room.get('members', []):
        room.setdefault('members', []).append(uid)
        room.setdefault('roles', {})[str(uid)] = 'member'
        save_data()
    return redirect(url_for('chat'))

# ------------------ SOCKETIO ОБРАБОТЧИКИ ------------------
def get_my_rooms(user_id):
    return [r for r in rooms if r.get('type') != 'dm' and (user_id == r.get('creator_id') or user_id in r.get('members', []))]

def get_dm_rooms(user_id):
    return [r for r in rooms if r.get('type') == 'dm' and user_id in r.get('members', [])]

@socketio.on('connect')
def handle_connect():
    user_id = session.get('user_id')
    if not user_id: return
    username, udata = get_user_by_id(user_id)
    if udata:
        udata['status'] = 'online'
        save_data()
        broadcast_status(user_id, 'online')

@socketio.on('disconnect')
def handle_disconnect():
    user_id = session.get('user_id')
    if user_id:
        username, udata = get_user_by_id(user_id)
        if udata:
            udata['status'] = 'offline'
            save_data()
            broadcast_status(user_id, 'offline')

@socketio.on('request_all_statuses')
def handle_request_all_statuses():
    user_id = session.get('user_id')
    if not user_id: return
    participants = set()
    for room in rooms:
        if room.get('type') == 'dm':
            if user_id in room.get('members', []):
                participants.update(room.get('members', []))
        else:
            if user_id == room.get('creator_id') or user_id in room.get('members', []):
                if room.get('creator_id'): participants.add(room['creator_id'])
                participants.update(room.get('members', []))
    participants.add(user_id)
    status_map = {}
    for uid in participants:
        uname, udata = get_user_by_id(uid)
        if uname:
            status_map[uname] = udata.get('status', 'offline')
    emit('all_statuses', status_map)

@socketio.on('set_status')
def handle_set_status(data):
    user_id = session.get('user_id')
    if not user_id: return
    new_status = data.get('status')
    if new_status not in ('online','offline','away','dnd'): return
    username, udata = get_user_by_id(user_id)
    if udata:
        udata['status'] = new_status
        save_data()
        broadcast_status(user_id, new_status)

@socketio.on('request_room_list')
def handle_request_room_list():
    user_id = session.get('user_id')
    if user_id:
        emit('room_list_update', get_my_rooms(user_id))

@socketio.on('get_dm_rooms')
def handle_get_dm_rooms():
    user_id = session.get('user_id')
    if user_id:
        emit('dm_rooms_update', get_dm_rooms(user_id))

@socketio.on('join')
def handle_join(data):
    user_id = session.get('user_id')
    if not user_id: return
    room_id = data['room_id']
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room: return
    if room.get('type') == 'dm':
        if user_id not in room.get('members', []): return
    else:
        if user_id != room.get('creator_id') and user_id not in room.get('members', []): return
    join_room(str(room_id))
    role = get_user_role(room, user_id)
    subrooms = room.get('subrooms', [{'id':1,'name':'общий'}])
    emit('room_info', {'id':room['id'],'name':room['name'],'type':room.get('type'),'description':room.get('description',''),'is_private':room.get('is_private',False),'invite_link':room.get('invite_link',''),'creator_id':room.get('creator_id'),'user_role':role,'subrooms':subrooms})

@socketio.on('load_subroom_messages')
def handle_load_subroom_messages(data):
    user_id = session.get('user_id')
    if not user_id: return
    room_id = data['room_id']; subroom_id = data['subroom_id']
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room: return
    if room.get('type') != 'dm' and user_id != room.get('creator_id') and user_id not in room.get('members', []): return
    msgs = messages.get(room_id, {}).get(subroom_id, [])[-50:]
    emit('subroom_history', msgs)

@socketio.on('load_older_messages')
def handle_load_older_messages(data):
    user_id = session.get('user_id')
    if not user_id: return
    room_id = data['room_id']; subroom_id = data['subroom_id']; before_msg_id = data.get('before_message_id')
    if not before_msg_id: return
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room: return
    if room.get('type') != 'dm' and user_id != room.get('creator_id') and user_id not in room.get('members', []): return
    all_msgs = messages.get(room_id, {}).get(subroom_id, [])
    idx = next((i for i,m in enumerate(all_msgs) if m['message_id'] == before_msg_id), None)
    if idx is None or idx == 0:
        emit('older_messages', [])
        return
    start = max(0, idx-50)
    older = all_msgs[start:idx]
    emit('older_messages', older)

@socketio.on('send_message')
def handle_send_message(data):
    user_id = session.get('user_id')
    if not user_id: return
    room_id = data['room_id']; subroom_id = data['subroom_id']; content = data['message'].strip(); is_image = data.get('is_image', False)
    if not content: return
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room: return
    if room.get('type') == 'dm':
        if user_id not in room.get('members', []): return
    else:
        if user_id != room.get('creator_id') and user_id not in room.get('members', []): return
    username = session['username']
    msg = {
        'message_id': int(datetime.now().timestamp() * 1000),
        'username': username,
        'content': content,
        'timestamp': datetime.now().strftime('%H:%M:%S'),
        'is_image': is_image,
        'room_id': room_id,
        'subroom_id': subroom_id
    }
    messages.setdefault(room_id, {}).setdefault(subroom_id, []).append(msg)
    if len(messages[room_id][subroom_id]) > 500:
        messages[room_id][subroom_id] = messages[room_id][subroom_id][-500:]
    save_data()
    emit('new_message', msg, to=str(room_id))

@socketio.on('delete_message')
def handle_delete_message(data):
    user_id = session.get('user_id')
    if not user_id: return
    room_id = data['room_id']; subroom_id = data['subroom_id']; msg_id = data['message_id']
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room or not can_delete_message(room, user_id): return
    if room_id in messages and subroom_id in messages[room_id]:
        messages[room_id][subroom_id] = [m for m in messages[room_id][subroom_id] if m.get('message_id') != msg_id]
        save_data()
        emit('message_deleted', {'subroom_id': subroom_id, 'message_id': msg_id}, to=str(room_id))

@socketio.on('create_room')
def handle_create_room(data):
    user_id = session.get('user_id')
    if not user_id: return
    name = data['name'].strip()
    if not name: return
    link = data.get('invite_link') or uuid.uuid4().hex[:8]
    while any(r.get('invite_link') == link for r in rooms):
        link = uuid.uuid4().hex[:8]
    room = {
        'id': len(rooms)+1,
        'name': name,
        'type': data.get('type','channel'),
        'creator_id': user_id,
        'is_private': data.get('is_private', False),
        'invite_link': link,
        'description': data.get('description', ''),
        'members': [user_id],
        'roles': {str(user_id): 'owner'},
        'subrooms': [{'id':1,'name':'общий'}]
    }
    rooms.append(room)
    messages[room['id']] = {1: []}
    save_data()
    emit('room_list_update', get_my_rooms(user_id))

@socketio.on('update_room')
def handle_update_room(data):
    user_id = session.get('user_id')
    if not user_id: return
    room_id = data['room_id']
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room or room.get('creator_id') != user_id: return
    room['name'] = data.get('name', room['name'])
    room['description'] = data.get('description', room.get('description',''))
    room['is_private'] = data.get('is_private', room.get('is_private'))
    new_link = data.get('invite_link')
    if new_link and new_link != room.get('invite_link'):
        if any(r.get('invite_link') == new_link for r in rooms if r['id'] != room_id):
            emit('join_error', 'Ссылка уже используется')
            return
        room['invite_link'] = new_link
    save_data()
    emit('room_list_update', get_my_rooms(user_id))
    emit('room_updated', to=str(room_id))

@socketio.on('search_rooms')
def handle_search(data):
    user_id = session.get('user_id')
    if not user_id: return
    query = data.get('query','').lower()
    results = []
    for room in rooms:
        if room.get('type') == 'dm': continue
        if user_id == room.get('creator_id') or user_id in room.get('members',[]): continue
        if (not room.get('is_private') and query in room['name'].lower()) or room.get('invite_link') == query:
            results.append({'name': room['name'], 'type': room['type'], 'invite_link': room['invite_link']})
    emit('search_results', results)

@socketio.on('join_by_invite')
def handle_join_by_invite(data):
    user_id = session.get('user_id')
    if not user_id: return
    room = next((r for r in rooms if r.get('invite_link') == data.get('invite_link')), None)
    if not room:
        emit('join_error', 'Ссылка не найдена')
        return
    if room.get('type') == 'dm':
        emit('join_error', 'Нельзя присоединиться к ЛС по ссылке')
        return
    if user_id != room.get('creator_id') and user_id not in room.get('members', []):
        room.setdefault('members', []).append(user_id)
        room.setdefault('roles', {})[str(user_id)] = 'member'
        save_data()
    emit('join_success', room)
    emit('room_list_update', get_my_rooms(user_id))

@socketio.on('search_users')
def handle_search_users(data):
    user_id = session.get('user_id')
    if not user_id: return
    query = data.get('query','').lower()
    results = [{'username': uname, 'id': udata['id']} for uname, udata in users.items() if udata['id'] != user_id and query in uname.lower()]
    emit('user_search_results', results)

@socketio.on('create_dm')
def handle_create_dm(data):
    user_id = session.get('user_id')
    if not user_id: return
    target_id = data.get('target_user_id')
    if not target_id or target_id == user_id: return
    existing = next((r for r in rooms if r.get('type') == 'dm' and set(r.get('members',[])) == {user_id, target_id}), None)
    if existing:
        emit('dm_created', existing)
        emit('dm_rooms_update', get_dm_rooms(user_id))
        return
    target_uname = next((uname for uname, udata in users.items() if udata['id'] == target_id), None)
    if not target_uname: return
    room_name = f"{session['username']} & {target_uname}"
    room = {
        'id': len(rooms)+1,
        'name': room_name,
        'type': 'dm',
        'creator_id': user_id,
        'members': [user_id, target_id],
        'is_private': True,
        'subrooms': [{'id':1,'name':'ЛС'}]
    }
    rooms.append(room)
    messages[room['id']] = {1: []}
    save_data()
    emit('dm_created', room)
    emit('dm_rooms_update', get_dm_rooms(user_id))

@socketio.on('get_room_members')
def handle_get_room_members(data):
    user_id = session.get('user_id')
    if not user_id: return
    room_id = data['room_id']
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room or room.get('type') == 'dm': return
    if user_id != room.get('creator_id') and user_id not in room.get('members', []): return
    members = []
    all_user_ids = set([room.get('creator_id')] + room.get('members', []))
    for uid in all_user_ids:
        uname, udata = get_user_by_id(uid)
        if uname:
            members.append({'user_id': uid, 'username': uname, 'role': get_user_role(room, uid)})
    emit('room_members', members)

@socketio.on('update_user_role')
def handle_update_user_role(data):
    user_id = session.get('user_id')
    if not user_id: return
    room_id = data['room_id']; target_id = data['target_user_id']; new_role = data['new_role']
    if new_role not in ('member','moderator','admin'): return
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room or room.get('type') == 'dm' or not can_manage_roles(room, user_id): return
    if target_id == room.get('creator_id'): return
    room.setdefault('roles', {})[str(target_id)] = new_role
    save_data()
    emit('room_updated', to=str(room_id))

@socketio.on('create_subroom')
def handle_create_subroom(data):
    user_id = session.get('user_id')
    if not user_id: return
    room_id = data['room_id']; name = data['name'].strip()
    if not name: return
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room or room.get('type') == 'dm' or not can_create_subrooms(room, user_id): return
    room.setdefault('subrooms', [{'id':1,'name':'общий'}])
    new_id = max([sr['id'] for sr in room['subrooms']]) + 1 if room['subrooms'] else 1
    new_sub = {'id': new_id, 'name': name}
    room['subrooms'].append(new_sub)
    messages.setdefault(room_id, {})[new_id] = []
    save_data()
    emit('subroom_created', new_sub, to=str(room_id))

@socketio.on('update_profile')
def handle_update_profile(data):
    user_id = session.get('user_id'); username = session.get('username')
    if not user_id or username not in users: return
    if data.get('avatar'):
        users[username]['avatar'] = data['avatar']
    users[username]['bio'] = data.get('bio', '')
    users[username]['theme'] = data.get('theme', 'dark')
    session['theme'] = data.get('theme', 'dark')
    save_data()
    socketio.emit('avatar_update', {'username': username, 'avatar': users[username]['avatar']})

@socketio.on('get_user_profile')
def handle_get_user_profile(data):
    target_username = data.get('username')
    if not target_username or target_username not in users:
        emit('user_profile', {'error': 'Пользователь не найден'})
        return
    u = users[target_username]
    emit('user_profile', {'username': target_username, 'bio': u.get('bio',''), 'avatar': u.get('avatar','')})

@socketio.on('report_bug')
def handle_report_bug(data):
    report = data.get('report')
    if report:
        with open(os.path.join(DATA_DIR, 'bugs.log'), 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().isoformat()} - {report}\n")
        emit('bug_received', {})

@socketio.on('leave')
def handle_leave(data):
    if data and 'room_id' in data:
        leave_room(str(data['room_id']))

# ------------------ ЗАПУСК ------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
