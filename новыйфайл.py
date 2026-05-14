import hashlib
import uuid
import json
import os
import base64
import signal
import sys
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, session
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecretkey'
socketio = SocketIO(app, async_mode='threading')

# ------------------ НАСТРОЙКА ПОСТОЯННОГО ХРАНИЛИЩА ------------------
DATA_DIR = '/data'                     # Папка постоянного диска на Render
os.makedirs(DATA_DIR, exist_ok=True)   # Создаём, если нет
DATA_FILE = os.path.join(DATA_DIR, 'data.json')

def load_data():
    if not os.path.exists(DATA_FILE):
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
        if 'subrooms' not in room:
            room['subrooms'] = [{'id': 1, 'name': 'общий'}]
        if 'roles' not in room:
            room['roles'] = {}
        if room.get('creator_id') and str(room['creator_id']) not in room['roles']:
            room['roles'][str(room['creator_id'])] = 'owner'
        if 'members' not in room:
            room['members'] = [room['creator_id']] if room.get('creator_id') else []
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

users, next_user_id, rooms, messages = load_data()

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
    roles = room.get('roles', {})
    return roles.get(str(user_id), 'member')

def can_manage_roles(room, user_id):
    role = get_user_role(room, user_id)
    return role in ('owner', 'admin')

def can_create_subrooms(room, user_id):
    return can_manage_roles(room, user_id)

def can_delete_message(room, user_id):
    role = get_user_role(room, user_id)
    return role in ('owner', 'admin', 'moderator')

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
    print(f"Получен сигнал {signum}, сохраняем данные...")
    save_data()
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown_signal_handler)
signal.signal(signal.SIGINT, shutdown_signal_handler)

# ------------------ ШАБЛОНЫ (без изменений) ------------------
LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Hovir — Вход</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1e1e1e, #2c2c2c);
            display: flex; justify-content: center; align-items: center; height: 100vh;
        }
        .auth-card {
            background: #2f3136; padding: 2rem; border-radius: 16px; width: 360px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.5); color: #dcddde;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .auth-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 12px 28px rgba(0,0,0,0.6);
        }
        .auth-card h2 {
            margin-bottom: 1.5rem; text-align: center;
            display: flex; align-items: center; justify-content: center; gap: 8px;
            color: #ffffff;
        }
        .auth-card h2::before { content: "🌼"; font-size: 1.8rem; animation: bounce 2s infinite; }
        @keyframes bounce { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-5px)} }
        input {
            width: 100%; padding: 12px; margin-bottom: 1rem;
            border: 2px solid #40444b; border-radius: 10px;
            background: #40444b; color: #dcddde; font-size: 0.95rem;
            transition: border 0.2s, background 0.2s;
        }
        input:focus { outline: none; border-color: #5865f2; background: #4f545c; }
        button {
            width: 100%; padding: 12px; background: #5865f2; color: white;
            border: none; border-radius: 10px; cursor: pointer;
            font-weight: 600; font-size: 1rem; transition: background 0.2s, transform 0.1s;
        }
        button:hover { background: #4752c4; transform: scale(1.02); }
        .link { text-align: center; margin-top: 1rem; color: #b9bbbe; }
        .link a { color: #00aff4; text-decoration: none; font-weight: 600; }
        .link a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="auth-card">
        <h2>Вход в Hovir</h2>
        <form method="POST">
            <input type="text" name="username" placeholder="Имя пользователя" required>
            <input type="password" name="password" placeholder="Пароль" required>
            <button type="submit">Войти</button>
        </form>
        <div class="link">Нет аккаунта? <a href="{{ url_for('register') }}">Регистрация</a></div>
    </div>
</body>
</html>
'''

REGISTER_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Hovir — Регистрация</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1e1e1e, #2c2c2c);
            display: flex; justify-content: center; align-items: center; height: 100vh;
        }
        .auth-card {
            background: #2f3136; padding: 2rem; border-radius: 16px; width: 360px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.5); color: #dcddde;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .auth-card:hover { transform: translateY(-4px); box-shadow: 0 12px 28px rgba(0,0,0,0.6); }
        .auth-card h2 {
            margin-bottom: 1.5rem; text-align: center;
            display: flex; align-items: center; justify-content: center; gap: 8px;
            color: #ffffff;
        }
        .auth-card h2::before { content: "🌼"; font-size: 1.8rem; animation: bounce 2s infinite; }
        @keyframes bounce { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-5px)} }
        input {
            width: 100%; padding: 12px; margin-bottom: 1rem;
            border: 2px solid #40444b; border-radius: 10px;
            background: #40444b; color: #dcddde; font-size: 0.95rem;
            transition: border 0.2s, background 0.2s;
        }
        input:focus { outline: none; border-color: #5865f2; background: #4f545c; }
        button {
            width: 100%; padding: 12px; background: #5865f2; color: white;
            border: none; border-radius: 10px; cursor: pointer;
            font-weight: 600; font-size: 1rem; transition: background 0.2s, transform 0.1s;
        }
        button:hover { background: #4752c4; transform: scale(1.02); }
        .link { text-align: center; margin-top: 1rem; color: #b9bbbe; }
        .link a { color: #00aff4; text-decoration: none; font-weight: 600; }
        .link a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="auth-card">
        <h2>Регистрация в Hovir</h2>
        <form method="POST">
            <input type="text" name="username" placeholder="Имя пользователя" required>
            <input type="password" name="password" placeholder="Пароль" required>
            <button type="submit">Зарегистрироваться</button>
        </form>
        <div class="link">Уже есть аккаунт? <a href="{{ url_for('login') }}">Войти</a></div>
    </div>
</body>
</html>
'''

CHAT_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Hovir — Чат</title>
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html, body { height: 100%; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: var(--bg);
            color: var(--text);
            display: flex;
            height: 100%;
            overflow: hidden;
            transition: background 0.3s, color 0.2s;
        }
        :root {
            --bg: #1e1e1e;
            --sidebar-bg: #2f3136;
            --text: #dcddde;
            --msg-bg: #40444b;
            --input-bg: #40444b;
            --border: #202225;
            --primary: #5865f2;
            --primary-hover: #4752c4;
            --sidebar-header-bg: #2f3136;
            --active-room-bg: #4f545c;
            --danger: #ed4245;
        }
        body.theme-green {
            --bg: #e8f5e9;
            --sidebar-bg: #ffffff;
            --text: #1b5e20;
            --msg-bg: #ffffff;
            --input-bg: #f1f8e9;
            --border: #c8e6c9;
            --primary: #4caf50;
            --primary-hover: #43a047;
            --sidebar-header-bg: linear-gradient(135deg, #66bb6a, #4caf50);
            --active-room-bg: #c8e6c9;
        }
        body.theme-light {
            --bg: #f5f5f5;
            --sidebar-bg: #ffffff;
            --text: #333;
            --msg-bg: #ffffff;
            --input-bg: #ffffff;
            --border: #ddd;
            --primary: #5865f2;
            --primary-hover: #4752c4;
            --sidebar-header-bg: #5865f2;
            --active-room-bg: #e0e0e0;
        }
        body.theme-flower {
            --bg: rgba(30, 20, 40, 0.85);
            --sidebar-bg: rgba(45, 35, 55, 0.75);
            --text: #fff0e0;
            --msg-bg: rgba(255, 255, 255, 0.2);
            --input-bg: rgba(255, 255, 255, 0.15);
            --border: rgba(255, 220, 180, 0.4);
            --primary: #ff9f4a;
            --primary-hover: #ffb86b;
            --sidebar-header-bg: linear-gradient(135deg, #ffad6a, #ff7e2e);
            --active-room-bg: rgba(255, 200, 120, 0.4);
            backdrop-filter: blur(2px);
        }
        body.theme-flower .message {
            backdrop-filter: blur(8px);
            background: rgba(255, 255, 255, 0.2);
            border: 1px solid rgba(255, 215, 150, 0.5);
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        body.theme-flower .sidebar, body.theme-flower .chat-header, body.theme-flower .input-area {
            backdrop-filter: blur(5px);
        }
        .floating-container {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 0;
        }
        .floating-emoji {
            position: absolute;
            font-size: 1.8rem;
            opacity: 0.5;
            animation: floatAround linear infinite;
            user-select: none;
            pointer-events: none;
        }
        @keyframes floatAround {
            0% { transform: translate(0, 0) rotate(0deg); opacity: 0.2; }
            50% { opacity: 0.7; }
            100% { transform: translate(var(--dx, 100px), var(--dy, -150px)) rotate(360deg); opacity: 0.2; }
        }
        @keyframes float {
            0% { transform: translateY(0px) rotate(0deg); }
            50% { transform: translateY(-20px) rotate(5deg); }
            100% { transform: translateY(0px) rotate(0deg); }
        }
        @keyframes pulse {
            0% { transform: scale(1); opacity: 0.7; }
            100% { transform: scale(1.2); opacity: 1; }
        }
        @keyframes slideDown {
            from { opacity: 0; transform: translateY(-50px); }
            to { opacity: 1; transform: translateY(0); }
        }
        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        
        .sidebar {
            width: 280px;
            background: var(--sidebar-bg);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            backdrop-filter: blur(2px);
            height: 100%;
        }
        .sidebar-header {
            padding: 16px;
            background: var(--sidebar-header-bg);
            color: white;
            font-weight: 700;
            font-size: 1.2rem;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .sidebar-header::before {
            content: "🌼";
            font-size: 1.6rem;
            animation: spin 8s linear infinite;
        }
        .view-switch {
            display: flex;
            gap: 4px;
            padding: 8px;
            border-bottom: 1px solid var(--border);
        }
        .view-switch button {
            flex: 1;
            padding: 8px;
            border: none;
            border-radius: 6px;
            background: var(--input-bg);
            color: var(--text);
            cursor: pointer;
            transition: 0.2s;
        }
        .view-switch button.active {
            background: var(--primary);
            color: white;
        }
        .room-list {
            list-style: none;
            flex: 1;
            overflow-y: auto;
            padding: 8px 0;
        }
        .room-item {
            padding: 10px 16px;
            margin: 2px 8px;
            border-radius: 10px;
            cursor: pointer;
            transition: 0.2s;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .room-item:hover {
            background: var(--input-bg);
            transform: translateX(3px);
        }
        .room-item.active {
            background: var(--active-room-bg);
            font-weight: 600;
        }
        .sidebar-buttons {
            padding: 8px;
            border-top: 1px solid var(--border);
            display: flex;
            gap: 4px;
        }
        .sidebar-buttons button {
            flex: 1;
            background: var(--primary);
            border: none;
            color: white;
            padding: 8px 12px;
            border-radius: 8px;
            cursor: pointer;
            transition: 0.2s;
        }
        .sidebar-buttons button:hover {
            background: var(--primary-hover);
            transform: translateY(-2px);
        }
        .user-info {
            padding: 12px 16px;
            border-top: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .user-name {
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .user-avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            object-fit: cover;
            background: var(--primary);
        }
        .status-select {
            background: var(--input-bg);
            color: var(--text);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 4px 8px;
            font-size: 0.75rem;
            cursor: pointer;
        }
        .settings-btn {
            cursor: pointer;
            font-size: 1.2rem;
            color: var(--primary);
            transition: transform 0.2s;
        }
        .settings-btn:hover {
            transform: rotate(15deg);
        }
        .logout {
            color: #d32f2f;
            text-decoration: none;
            font-weight: 600;
            margin-left: 8px;
        }
        .logout:hover {
            color: #ff5252;
        }
        .chat-area {
            flex: 1;
            display: flex;
            flex-direction: column;
            height: 100%;
            overflow: hidden;
            position: relative;
            z-index: 1;
        }
        .chat-header {
            padding: 12px 20px;
            background: var(--sidebar-bg);
            border-bottom: 1px solid var(--border);
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
            backdrop-filter: blur(2px);
            flex-shrink: 0;
        }
        .subroom-selector {
            background: var(--input-bg);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 6px 12px;
            color: var(--text);
            cursor: pointer;
        }
        .status-badge {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 6px;
        }
        .status-online { background-color: #2ecc71; animation: pulse 1.5s infinite; }
        .status-offline { background-color: #7f8c8d; }
        .status-away { background-color: #f1c40f; }
        .status-dnd { background-color: #e74c3c; }
        .chat-content {
            flex: 1;
            overflow-y: auto;
            min-height: 0;
            display: flex;
            flex-direction: column;
        }
        .messages {
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .message {
            max-width: 75%;
            padding: 10px 14px;
            border-radius: 18px;
            background: var(--msg-bg);
            align-self: flex-start;
            position: relative;
            transition: 0.1s;
            animation: slideDown 0.2s ease;
        }
        .message:hover {
            transform: scale(1.01);
        }
        .message-header {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.85rem;
        }
        .message-user {
            font-weight: 700;
            color: var(--primary);
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 5px;
        }
        .message-time {
            color: #81c784;
            font-size: 0.75rem;
        }
        .message-image {
            max-width: 100%;
            max-height: 300px;
            border-radius: 12px;
            margin-top: 6px;
            cursor: pointer;
        }
        .delete-msg {
            position: absolute;
            right: 8px;
            top: 8px;
            background: none;
            border: none;
            color: var(--danger);
            cursor: pointer;
            opacity: 0;
            transition: 0.2s;
        }
        .message:hover .delete-msg {
            opacity: 1;
        }
        .input-area {
            flex-shrink: 0;
            padding: 12px 20px;
            background: var(--sidebar-bg);
            border-top: 1px solid var(--border);
            display: flex;
            gap: 10px;
            backdrop-filter: blur(2px);
        }
        .input-area textarea {
            flex: 1;
            padding: 12px 16px;
            border: 2px solid var(--border);
            border-radius: 24px;
            background: var(--input-bg);
            color: var(--text);
            font-family: inherit;
            font-size: inherit;
            resize: none;
            overflow-y: auto;
            max-height: 120px;
            line-height: 1.4;
        }
        .input-area textarea:focus {
            outline: none;
            border-color: var(--primary);
        }
        .input-area button {
            background: var(--primary);
            border: none;
            padding: 0 20px;
            border-radius: 24px;
            color: white;
            font-weight: 600;
            cursor: pointer;
            transition: 0.2s;
        }
        .input-area button:hover {
            background: var(--primary-hover);
            transform: scale(1.02);
        }
        .file-input-label {
            background: var(--primary);
            border: none;
            padding: 0 16px;
            border-radius: 24px;
            color: white;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: 0.2s;
        }
        .file-input-label:hover {
            background: var(--primary-hover);
            transform: scale(1.02);
        }
        .file-input-label input {
            display: none;
        }
        .welcome-screen {
            flex: 1;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            text-align: center;
            position: relative;
            overflow: hidden;
            background: linear-gradient(135deg, rgba(88,101,242,0.05), rgba(88,101,242,0.1));
        }
        .floating-flower {
            position: absolute;
            font-size: 2rem;
            opacity: 0.3;
            animation: float 8s infinite ease-in-out;
            pointer-events: none;
        }
        .welcome-logo {
            font-size: 5rem;
            animation: pulse 1.2s infinite alternate, spin 6s linear infinite;
            display: inline-block;
            margin-bottom: 1rem;
        }
        .welcome-screen h1 {
            font-size: 2.5rem;
            background: linear-gradient(135deg, var(--primary), #a29bfe);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
        }
        .features-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            max-width: 800px;
            margin-top: 2rem;
        }
        .feature-card {
            background: var(--sidebar-bg);
            padding: 1rem;
            border-radius: 16px;
            border: 1px solid var(--border);
            transition: 0.3s;
        }
        .feature-card:hover {
            transform: translateY(-5px);
        }
        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.7);
            backdrop-filter: blur(4px);
            animation: fadeIn 0.2s;
        }
        .modal-content {
            background: var(--sidebar-bg);
            margin: 5% auto;
            padding: 24px;
            border-radius: 20px;
            width: 550px;
            max-width: 90%;
            position: relative;
            animation: slideDown 0.3s;
            max-height: 85vh;
            overflow-y: auto;
        }
        .close-modal {
            position: absolute;
            right: 20px;
            top: 16px;
            font-size: 28px;
            font-weight: bold;
            cursor: pointer;
            transition: 0.2s;
            z-index: 1;
        }
        .close-modal:hover {
            color: var(--danger);
            transform: scale(1.1);
        }
        .modal-content h2 {
            margin-bottom: 20px;
            padding-right: 30px;
            border-bottom: 2px solid var(--primary);
            display: inline-block;
        }
        .settings-tabs {
            display: flex;
            gap: 8px;
            margin-bottom: 24px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 12px;
        }
        .settings-tab {
            padding: 8px 20px;
            background: var(--input-bg);
            border: none;
            border-radius: 20px;
            cursor: pointer;
            color: var(--text);
            transition: 0.2s;
            font-weight: 500;
        }
        .settings-tab.active {
            background: var(--primary);
            color: white;
        }
        .settings-tab:hover:not(.active) {
            background: var(--primary-hover);
            color: white;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
            animation: fadeIn 0.3s;
        }
        .settings-group {
            margin-bottom: 20px;
        }
        .settings-group label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            color: var(--primary);
        }
        .settings-group input, .settings-group select, .settings-group textarea {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid var(--border);
            border-radius: 12px;
            background: var(--input-bg);
            color: var(--text);
            font-size: 0.95rem;
        }
        .settings-group input:focus, .settings-group select:focus, .settings-group textarea:focus {
            outline: none;
            border-color: var(--primary);
        }
        .avatar-upload {
            text-align: center;
            margin-bottom: 20px;
        }
        .avatar-preview {
            width: 100px;
            height: 100px;
            border-radius: 50%;
            object-fit: cover;
            margin: 10px auto;
            display: block;
            background: var(--input-bg);
            border: 3px solid var(--primary);
            cursor: pointer;
        }
        .help-section {
            padding: 12px;
        }
        .help-category {
            margin-bottom: 20px;
        }
        .help-category h3 {
            color: var(--primary);
            margin-bottom: 12px;
            font-size: 1.1rem;
        }
        .help-item {
            padding: 8px 0;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .help-icon {
            font-size: 1.3rem;
            min-width: 32px;
        }
        .help-text {
            flex: 1;
        }
        .help-text strong {
            display: block;
            margin-bottom: 4px;
        }
        .help-text small {
            color: #888;
            font-size: 0.8rem;
        }
        .bug-form textarea {
            min-height: 100px;
            resize: vertical;
        }
        .modal-actions {
            display: flex;
            gap: 12px;
            margin-top: 24px;
            justify-content: flex-end;
        }
        .btn-primary {
            background: var(--primary);
            color: white;
            border: none;
            padding: 10px 24px;
            border-radius: 24px;
            cursor: pointer;
            transition: 0.2s;
        }
        .btn-primary:hover {
            background: var(--primary-hover);
            transform: translateY(-2px);
        }
        .btn-cancel {
            background: #aaa;
            color: white;
            border: none;
            padding: 10px 24px;
            border-radius: 24px;
            cursor: pointer;
            transition: 0.2s;
        }
        .btn-cancel:hover {
            background: #999;
        }
        .role-badge {
            font-size: 0.7rem;
            padding: 2px 6px;
            border-radius: 12px;
            background: var(--primary);
            color: white;
            margin-left: 6px;
        }
        .user-list-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px;
            border-bottom: 1px solid var(--border);
        }
        .role-select {
            width: 130px;
            padding: 4px;
        }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
        .loading-indicator {
            text-align: center;
            padding: 10px;
            color: var(--primary);
            font-size: 0.8rem;
            display: none;
        }
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
                <img id="sidebar-avatar" class="user-avatar" src="{{ user_avatar }}" onerror="this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22%3E%3Ccircle cx=%2250%22 cy=%2250%22 r=%2250%22 fill=%22%235865f2%22/%3E%3Ctext x=%2250%22 y=%2267%22 text-anchor=%22middle%22 fill=%22white%22 font-size=%2245%22%3E{{ username[0]|upper }}%3C/text%3E%3C/svg%3E'">
                <span>{{ username }}</span>
                <select id="status-select" class="status-select" onchange="changeStatus(this.value)">
                    <option value="online">🟢 Онлайн</option>
                    <option value="away">🌙 Отошёл</option>
                    <option value="dnd">⛔ Не беспокоить</option>
                    <option value="offline">⚫ Не в сети</option>
                </select>
            </div>
            <div>
                <span class="settings-btn" onclick="openProfileSettings()">⚙️</span>
                <a href="{{ url_for('logout') }}" class="logout">🚪</a>
            </div>
        </div>
    </div>

    <div class="chat-area">
        <div class="chat-header">
            <span id="room-title">Hovir</span>
            <select id="subroom-selector" class="subroom-selector" style="display:none;" onchange="changeSubroom()"></select>
            <span id="room-settings-btn" class="room-settings" style="display:none;" onclick="openRoomSettings()">⚙️</span>
            <span id="manage-roles-btn" class="room-settings" style="display:none;" onclick="openManageRoles()">👥 Роли</span>
            <span id="create-subroom-btn" class="room-settings" style="display:none;" onclick="openCreateSubroom()">➕ Подканал</span>
        </div>

        <div id="welcome-screen" class="welcome-screen">
            <div class="floating-flower" style="top:10%; left:5%; animation-duration:12s;">🌸</div>
            <div class="floating-flower" style="bottom:15%; right:8%; animation-duration:14s;">🌼</div>
            <div class="floating-flower" style="top:30%; right:15%; animation-duration:10s;">🌻</div>
            <div class="floating-flower" style="bottom:25%; left:12%; animation-duration:16s;">🌺</div>
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

        <div id="chat-interface" style="display:none; flex-direction: column; flex:1; min-height:0;">
            <div class="chat-content" id="messages-container">
                <div class="messages" id="messages"></div>
                <div id="loading-older" class="loading-indicator">⏳ Загрузка старых сообщений...</div>
            </div>
            <div class="input-area">
                <textarea id="message-input" placeholder="Введите сообщение..." rows="1" disabled></textarea>
                <label class="file-input-label">
                    📷 Фото
                    <input type="file" id="image-input" accept="image/jpeg,image/png,image/gif" disabled>
                </label>
                <button id="send-btn" disabled>➤ Отправить</button>
            </div>
        </div>
    </div>

    <!-- Модальные окна -->
    <div id="create-modal" class="modal">
        <div class="modal-content">
            <span class="close-modal" onclick="closeModal('create-modal')">&times;</span>
            <h2 id="create-modal-title">Создать</h2>
            <label>Название</label>
            <input id="create-name">
            <label>Описание</label>
            <textarea id="create-desc" rows="2"></textarea>
            <label>Тип</label>
            <select id="create-type">
                <option value="channel">Канал</option>
                <option value="group">Группа</option>
            </select>
            <label><input type="checkbox" id="create-private"> Приватный</label>
            <label>Ссылка-приглашение</label>
            <input id="create-link">
            <div class="modal-actions">
                <button class="btn-cancel" onclick="closeModal('create-modal')">Отмена</button>
                <button class="btn-primary" onclick="submitCreateRoom()">Создать</button>
            </div>
        </div>
    </div>
    <div id="search-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('search-modal')">&times;</span><h2>Поиск каналов</h2><input id="search-query" placeholder="Название или ссылка"><button class="btn-primary" onclick="searchRooms()">Искать</button><div id="search-results"></div></div></div>
    <div id="user-search-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('user-search-modal')">&times;</span><h2>Поиск пользователей</h2><input id="user-search-query"><button class="btn-primary" onclick="searchUsers()">Искать</button><div id="user-search-results"></div></div></div>
    <div id="room-settings-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('room-settings-modal')">&times;</span><h2>Настройки комнаты</h2><label>Название</label><input id="rs-name"><label>Описание</label><textarea id="rs-desc" rows="2"></textarea><label>Приватность</label><select id="rs-private"><option value="0">Публичная</option><option value="1">Приватная</option></select><label>Ссылка</label><input id="rs-link"><div class="modal-actions"><button class="btn-cancel" onclick="closeModal('room-settings-modal')">Отмена</button><button class="btn-primary" onclick="submitRoomSettings()">Сохранить</button></div></div></div>
    <div id="settings-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('settings-modal')">&times;</span><h2>Настройки</h2><div class="settings-tabs"><button class="settings-tab active" onclick="switchSettingsTab('profile')">👤 Профиль</button><button class="settings-tab" onclick="switchSettingsTab('appearance')">🎨 Внешний вид</button><button class="settings-tab" onclick="switchSettingsTab('help')">❓ Справка</button><button class="settings-tab" onclick="switchSettingsTab('bug')">🐛 Сообщить об ошибке</button></div><div id="profile-tab" class="tab-content active"><div class="avatar-upload"><img id="settings-avatar-preview" class="avatar-preview" src="{{ user_avatar }}" onclick="document.getElementById('settings-avatar-input').click()" onerror="this.style.display='none'"><input type="file" id="settings-avatar-input" accept="image/jpeg,image/png,image/gif" style="display:none"></div><div class="settings-group"><label>Имя пользователя</label><input value="{{ username }}" disabled></div><div class="settings-group"><label>О себе</label><textarea id="profile-bio" rows="3">{{ user_bio }}</textarea></div></div><div id="appearance-tab" class="tab-content"><div class="settings-group"><label>Тема оформления</label><select id="profile-theme"><option value="dark">🌙 Тёмная</option><option value="green">🍃 Зелёная</option><option value="light">☀️ Светлая</option><option value="flower">🌸 Цветочная (летающие цветы)</option></select></div><div class="settings-group"><label>Размер шрифта</label><select id="font-size"><option value="small">Маленький</option><option value="medium" selected>Средний</option><option value="large">Большой</option></select></div><div class="settings-group"><label>🔊 Звук уведомлений</label><select id="notification-sound"><option value="on">Включить</option><option value="off">Выключить</option></select></div></div><div id="help-tab" class="tab-content"><div class="help-section"><div class="help-category"><h3>📌 Основные функции</h3><div class="help-item"><div class="help-icon">📢</div><div class="help-text"><strong>Каналы и группы</strong><small>Публичные или приватные комнаты для общения</small></div></div><div class="help-item"><div class="help-icon">👑</div><div class="help-text"><strong>Роли участников</strong><small>Владелец может назначать администраторов и модераторов</small></div></div><div class="help-item"><div class="help-icon">➕</div><div class="help-text"><strong>Подканалы</strong><small>Создаются администраторами для разделения тем</small></div></div><div class="help-item"><div class="help-icon">🖼️</div><div class="help-text"><strong>Отправка фото</strong><small>Нажмите 📷 Фото и выберите изображение (до 5 МБ)</small></div></div></div><div class="help-category"><h3>💬 Общение</h3><div class="help-item"><div class="help-icon">✉️</div><div class="help-text"><strong>Личные сообщения</strong><small>Ищите пользователей и начинайте диалог</small></div></div><div class="help-item"><div class="help-icon">🔍</div><div class="help-text"><strong>Поиск каналов</strong><small>Находите публичные комнаты по названию или ссылке</small></div></div><div class="help-item"><div class="help-icon">🎨</div><div class="help-text"><strong>Темы оформления</strong><small>Тёмная, зелёная, светлая, цветочная (с летающими цветами)</small></div></div></div><div class="help-category"><h3>⚙️ Управление</h3><div class="help-item"><div class="help-icon">⚙️</div><div class="help-text"><strong>Настройки комнаты</strong><small>Доступны владельцу</small></div></div><div class="help-item"><div class="help-icon">💾</div><div class="help-text"><strong>Аватар</strong><small>Загрузите свою картинку (jpg/png/gif, до 2 МБ)</small></div></div><div class="help-item"><div class="help-icon">🛡️</div><div class="help-text"><strong>Модерация</strong><small>Администраторы и модераторы могут удалять сообщения</small></div></div></div></div></div><div id="bug-tab" class="tab-content"><div class="bug-form"><div class="settings-group"><label>Тема ошибки</label><input id="bug-subject" placeholder="Кратко опишите проблему"></div><div class="settings-group"><label>Подробное описание</label><textarea id="bug-description" rows="4" placeholder="Что произошло? Какие действия привели к ошибке?"></textarea></div><div class="settings-group"><label>Ваш email (необязательно)</label><input id="bug-email" placeholder="email@example.com"></div></div></div><div class="modal-actions"><button class="btn-cancel" onclick="closeModal('settings-modal')">Закрыть</button><button class="btn-primary" onclick="saveAllSettings()">Сохранить изменения</button></div></div></div>
    <div id="user-profile-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('user-profile-modal')">&times;</span><h2>Профиль пользователя</h2><div id="user-profile-content"></div><div class="modal-actions"><button class="btn-primary" onclick="closeModal('user-profile-modal')">Закрыть</button></div></div></div>
    <div id="manage-roles-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('manage-roles-modal')">&times;</span><h2>Управление ролями</h2><div id="roles-list"></div><div class="modal-actions"><button class="btn-cancel" onclick="closeModal('manage-roles-modal')">Закрыть</button></div></div></div>
    <div id="create-subroom-modal" class="modal"><div class="modal-content"><span class="close-modal" onclick="closeModal('create-subroom-modal')">&times;</span><h2>Создать подканал</h2><label>Название</label><input id="subroom-name"><div class="modal-actions"><button class="btn-cancel" onclick="closeModal('create-subroom-modal')">Отмена</button><button class="btn-primary" onclick="submitCreateSubroom()">Создать</button></div></div></div>

    <div id="floating-container" class="floating-container" style="display: none;"></div>

    <script>
        const socket = io();
        let currentRoomId = null;
        let currentSubroomId = null;
        let currentRoomSettings = null;
        let currentView = 'rooms';
        const username = "{{ username }}";
        const userId = {{ user_id }};
        let userStatuses = {};
        let currentDmPartner = null;
        let currentSubrooms = [];
        let pendingAvatarBase64 = null;
        let oldestMessageId = null;
        let hasMoreMessages = true;
        let loadingOlder = false;
        let isWindowFocused = true;

        let unreadTotal = 0;
        let unreadPerRoom = {};

        function updateTitle() {
            if (unreadTotal > 0) {
                document.title = `📩 (${unreadTotal}) Hovir — Чат`;
            } else {
                document.title = `Hovir — Чат`;
            }
        }

        function updateRoomHeaderIndicator(roomId) {
            const roomTitleSpan = document.getElementById('room-title');
            if (!roomTitleSpan) return;
            let baseName = currentRoomSettings ? currentRoomSettings.name : 'Hovir';
            const unread = unreadPerRoom[roomId] || 0;
            if (unread > 0) {
                roomTitleSpan.innerHTML = `${baseName} • ${unread}`;
            } else {
                roomTitleSpan.innerHTML = baseName;
            }
        }

        function resetUnreadForRoom(roomId) {
            if (unreadPerRoom[roomId]) {
                unreadTotal -= unreadPerRoom[roomId];
                delete unreadPerRoom[roomId];
                if (unreadTotal < 0) unreadTotal = 0;
                updateTitle();
                updateRoomHeaderIndicator(roomId);
            }
        }

        function addUnreadForRoom(roomId, count = 1) {
            if (!unreadPerRoom[roomId]) unreadPerRoom[roomId] = 0;
            unreadPerRoom[roomId] += count;
            unreadTotal += count;
            updateTitle();
            if (currentRoomId === roomId) {
                resetUnreadForRoom(roomId);
            } else {
                updateRoomHeaderIndicator(roomId);
            }
        }

        function playNotificationSound() {
            const soundEnabled = localStorage.getItem('notificationSound') !== 'off';
            if (!soundEnabled) return;
            try {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                const oscillator = audioCtx.createOscillator();
                const gainNode = audioCtx.createGain();
                oscillator.connect(gainNode);
                gainNode.connect(audioCtx.destination);
                oscillator.frequency.value = 800;
                gainNode.gain.value = 0.2;
                oscillator.start();
                gainNode.gain.exponentialRampToValueAtTime(0.00001, audioCtx.currentTime + 0.3);
                oscillator.stop(audioCtx.currentTime + 0.3);
                if (audioCtx.state === 'suspended') audioCtx.resume();
            } catch(e) { console.warn('Web Audio API не поддерживается', e); }
        }

        window.addEventListener('focus', () => {
            isWindowFocused = true;
        });
        window.addEventListener('blur', () => { isWindowFocused = false; });

        function loadSoundSetting() {
            const saved = localStorage.getItem('notificationSound');
            if (saved === 'on' || saved === 'off') {
                document.getElementById('notification-sound').value = saved;
            } else {
                document.getElementById('notification-sound').value = 'on';
                localStorage.setItem('notificationSound', 'on');
            }
        }

        const roomList = document.getElementById('room-list');
        const messagesDiv = document.getElementById('messages');
        const roomTitle = document.getElementById('room-title');
        const roomSettingsBtn = document.getElementById('room-settings-btn');
        const manageRolesBtn = document.getElementById('manage-roles-btn');
        const createSubroomBtn = document.getElementById('create-subroom-btn');
        const subroomSelector = document.getElementById('subroom-selector');
        const messageTextarea = document.getElementById('message-input');
        const sendBtn = document.getElementById('send-btn');
        const welcomeScreen = document.getElementById('welcome-screen');
        const chatInterface = document.getElementById('chat-interface');
        const imageInput = document.getElementById('image-input');
        const messagesContainer = document.getElementById('messages-container');
        const loadingIndicator = document.getElementById('loading-older');

        function scrollToBottom() {
            if (!messagesContainer) return;
            setTimeout(() => {
                messagesContainer.scrollTop = messagesContainer.scrollHeight;
            }, 20);
            requestAnimationFrame(() => {
                messagesContainer.scrollTop = messagesContainer.scrollHeight;
            });
        }

        function autoResizeTextarea() {
            if (messageTextarea) {
                messageTextarea.style.height = 'auto';
                let newHeight = Math.min(messageTextarea.scrollHeight, 120);
                messageTextarea.style.height = newHeight + 'px';
            }
        }

        function loadOlderMessages() {
            if (loadingOlder || !hasMoreMessages || !currentRoomId || currentSubroomId === null) return;
            if (!oldestMessageId) return;
            loadingOlder = true;
            loadingIndicator.style.display = 'block';
            socket.emit('load_older_messages', {
                room_id: currentRoomId,
                subroom_id: currentSubroomId,
                before_message_id: oldestMessageId
            });
        }

        function onChatScroll() {
            if (!messagesContainer) return;
            if (messagesContainer.scrollTop <= 50 && !loadingOlder && hasMoreMessages) {
                loadOlderMessages();
            }
        }

        function startFloatingElements() {
            const container = document.getElementById('floating-container');
            if (!container) return;
            container.innerHTML = '';
            if (document.body.classList.contains('theme-flower')) {
                container.style.display = 'block';
                const emojis = ['🌸', '🌼', '🌻', '🌺', '🦋', '🐞', '✨', '💐', '🌷', '😊', '⭐', '🍃'];
                for (let i = 0; i < 35; i++) {
                    const emoji = document.createElement('div');
                    emoji.className = 'floating-emoji';
                    emoji.textContent = emojis[Math.floor(Math.random() * emojis.length)];
                    const size = 20 + Math.random() * 30;
                    emoji.style.fontSize = size + 'px';
                    emoji.style.left = Math.random() * 100 + '%';
                    emoji.style.top = Math.random() * 100 + '%';
                    const duration = 12 + Math.random() * 20;
                    const dx = (Math.random() - 0.5) * 200;
                    const dy = (Math.random() - 0.5) * 200 - 50;
                    emoji.style.setProperty('--dx', dx + 'px');
                    emoji.style.setProperty('--dy', dy + 'px');
                    emoji.style.animationDuration = duration + 's';
                    emoji.style.animationDelay = Math.random() * 5 + 's';
                    container.appendChild(emoji);
                }
            } else {
                container.style.display = 'none';
            }
        }

        function stopFloatingElements() {
            const container = document.getElementById('floating-container');
            if (container) container.innerHTML = '';
        }

        function applyTheme(theme) {
            document.body.className = 'theme-' + theme;
            if (theme === 'flower') startFloatingElements();
            else stopFloatingElements();
        }

        function getStatusIcon(status) {
            const cls = { online:'status-online', offline:'status-offline', away:'status-away', dnd:'status-dnd' }[status] || 'status-offline';
            return `<span class="status-badge ${cls}"></span>`;
        }

        function updateMessageStatusesForUser(targetUsername, newStatus) {
            document.querySelectorAll('.message').forEach(msg => {
                const userSpan = msg.querySelector('.message-user');
                if (userSpan && userSpan.textContent.trim() === targetUsername) {
                    let existing = userSpan.querySelector('.status-badge');
                    const newIcon = getStatusIcon(newStatus);
                    if (existing) existing.outerHTML = newIcon;
                    else userSpan.insertAdjacentHTML('afterbegin', newIcon);
                }
            });
        }

        function onStatusUpdate(data) {
            userStatuses[data.username] = data.status;
            updateMessageStatusesForUser(data.username, data.status);
            if (currentRoomSettings?.type === 'dm' && currentDmPartner === data.username) updateChatHeaderStatus();
        }

        function changeStatus(newStatus) {
            socket.emit('set_status', { status: newStatus });
            userStatuses[username] = newStatus;
        }

        function switchView(view) {
            currentView = view;
            document.getElementById('btn-rooms').classList.toggle('active', view === 'rooms');
            document.getElementById('btn-dm').classList.toggle('active', view === 'dm');
            document.getElementById('sidebar-buttons-rooms').style.display = view === 'rooms' ? 'flex' : 'none';
            document.getElementById('sidebar-buttons-dm').style.display = view === 'dm' ? 'flex' : 'none';
            if (currentRoomId) {
                socket.emit('leave', { room_id: currentRoomId });
                currentRoomId = null;
                currentSubroomId = null;
            }
            welcomeScreen.style.display = 'flex';
            chatInterface.style.display = 'none';
            roomTitle.textContent = 'Hovir';
            roomSettingsBtn.style.display = 'none';
            manageRolesBtn.style.display = 'none';
            createSubroomBtn.style.display = 'none';
            subroomSelector.style.display = 'none';
            messagesDiv.innerHTML = '';
            if (view === 'rooms') socket.emit('request_room_list');
            else if (view === 'dm') socket.emit('get_dm_rooms');
        }

        function updateList(rooms) {
            roomList.innerHTML = '';
            rooms.forEach(room => {
                const li = document.createElement('li');
                li.className = 'room-item';
                li.dataset.roomId = room.id;
                li.dataset.roomName = room.name;
                let icon = '';
                if (room.type === 'group') icon = ' 👥';
                else if (room.type === 'channel') icon = ' 📢';
                else if (room.type === 'dm') icon = ' ✉️';
                if (room.type === 'dm') {
                    const names = room.name.split(' & ');
                    const partner = names[0] === username ? names[1] : names[0];
                    const statusHtml = getStatusIcon(userStatuses[partner] || 'offline');
                    li.innerHTML = `<span class="dm-status-badge" style="margin-right:6px;">${statusHtml}</span>${room.name}${icon}`;
                } else {
                    li.textContent = room.name + icon;
                }
                roomList.appendChild(li);
            });
            bindRoomClicks();
        }

        function bindRoomClicks() {
            document.querySelectorAll('.room-item').forEach(item => {
                item.addEventListener('click', () => {
                    const roomId = parseInt(item.dataset.roomId);
                    document.querySelectorAll('.room-item').forEach(r => r.classList.remove('active'));
                    item.classList.add('active');
                    joinRoom(roomId);
                });
            });
        }

        function joinRoom(roomId) {
            if (currentRoomId) {
                socket.emit('leave', { room_id: currentRoomId });
            }
            currentRoomId = roomId;
            resetUnreadForRoom(roomId);
            welcomeScreen.style.display = 'none';
            chatInterface.style.display = 'flex';
            messageTextarea.disabled = false;
            sendBtn.disabled = false;
            imageInput.disabled = false;
            messagesDiv.innerHTML = '';
            oldestMessageId = null;
            hasMoreMessages = true;
            loadingOlder = false;
            if (messagesContainer) {
                messagesContainer.removeEventListener('scroll', onChatScroll);
                messagesContainer.addEventListener('scroll', onChatScroll);
            }
            socket.emit('join', { room_id: roomId });
        }

        socket.on('room_info', (data) => {
            currentRoomSettings = data;
            currentSubrooms = data.subrooms || [{ id: 1, name: 'общий' }];
            let titleText = data.name;
            const unread = unreadPerRoom[data.id] || 0;
            if (unread > 0) titleText += ` • ${unread}`;
            roomTitle.textContent = titleText;
            if (data.type !== 'dm') {
                const myRole = data.user_role;
                manageRolesBtn.style.display = (myRole === 'owner' || myRole === 'admin') ? 'inline' : 'none';
                createSubroomBtn.style.display = (myRole === 'owner' || myRole === 'admin') ? 'inline' : 'none';
                roomSettingsBtn.style.display = (myRole === 'owner') ? 'inline' : 'none';
                subroomSelector.innerHTML = '';
                currentSubrooms.forEach(sr => {
                    const option = document.createElement('option');
                    option.value = sr.id;
                    option.textContent = sr.name;
                    subroomSelector.appendChild(option);
                });
                subroomSelector.style.display = 'inline-block';
                if (currentSubroomId === null && currentSubrooms.length) currentSubroomId = currentSubrooms[0].id;
                subroomSelector.value = currentSubroomId;
                loadMessagesForSubroom();
            } else {
                const names = data.name.split(' & ');
                currentDmPartner = names[0] === username ? names[1] : names[0];
                updateChatHeaderStatus();
                currentSubroomId = 1;
                loadMessagesForSubroom();
                manageRolesBtn.style.display = 'none';
                createSubroomBtn.style.display = 'none';
                subroomSelector.style.display = 'none';
            }
        });

        function loadMessagesForSubroom() {
            if (!currentRoomId || currentSubroomId === null) return;
            oldestMessageId = null;
            hasMoreMessages = true;
            loadingOlder = false;
            messagesDiv.innerHTML = '';
            socket.emit('load_subroom_messages', { room_id: currentRoomId, subroom_id: currentSubroomId });
        }

        socket.on('subroom_history', (history) => {
            messagesDiv.innerHTML = '';
            if (history.length > 0) {
                history.forEach(msg => addMessage(msg, false));
                oldestMessageId = history[0].message_id;
                hasMoreMessages = (history.length === 50);
            } else {
                hasMoreMessages = false;
            }
            scrollToBottom();
        });

        socket.on('older_messages', (olderMsgs) => {
            if (!olderMsgs || olderMsgs.length === 0) {
                hasMoreMessages = false;
                loadingIndicator.style.display = 'none';
                loadingOlder = false;
                return;
            }
            const oldScrollHeight = messagesContainer.scrollHeight;
            const oldScrollTop = messagesContainer.scrollTop;
            olderMsgs.forEach(msg => addMessage(msg, true));
            oldestMessageId = olderMsgs[0].message_id;
            const newScrollHeight = messagesContainer.scrollHeight;
            messagesContainer.scrollTop = oldScrollTop + (newScrollHeight - oldScrollHeight);
            hasMoreMessages = (olderMsgs.length === 50);
            loadingIndicator.style.display = 'none';
            loadingOlder = false;
        });

        socket.on('new_message', (data) => {
            if (data.username !== username) {
                if (currentRoomId !== data.room_id) {
                    addUnreadForRoom(data.room_id, 1);
                }
                if (!isWindowFocused) {
                    playNotificationSound();
                }
            }
            if (data.subroom_id === currentSubroomId && currentRoomId === data.room_id) {
                addMessage(data, false);
                scrollToBottom();
                if (!oldestMessageId) oldestMessageId = data.message_id;
            }
        });

        function addMessage(msg, prepend = false) {
            const div = document.createElement('div');
            div.className = 'message';
            div.dataset.msgId = msg.message_id;
            const statusIcon = getStatusIcon(userStatuses[msg.username] || 'offline');
            let delBtn = '';
            if (currentRoomSettings && (currentRoomSettings.user_role === 'owner' || currentRoomSettings.user_role === 'admin' || currentRoomSettings.user_role === 'moderator')) {
                delBtn = `<button class="delete-msg" onclick="deleteMessage(${msg.message_id})">🗑️</button>`;
            }
            let contentHtml = '';
            if (msg.is_image) {
                contentHtml = `<img src="${escapeHtml(msg.content)}" class="message-image" onclick="window.open(this.src)" alt="image">`;
            } else {
                contentHtml = `<div class="message-text">${escapeHtml(msg.content)}</div>`;
            }
            div.innerHTML = `<div class="message-header"><span class="message-user" onclick="openUserProfile('${escapeHtml(msg.username)}')">${statusIcon}${escapeHtml(msg.username)}</span><span class="message-time">${msg.timestamp}</span></div>${contentHtml}${delBtn}`;
            if (prepend) {
                messagesDiv.insertBefore(div, messagesDiv.firstChild);
            } else {
                messagesDiv.appendChild(div);
            }
        }

        function deleteMessage(msgId) {
            if (confirm('Удалить сообщение?'))
                socket.emit('delete_message', { room_id: currentRoomId, subroom_id: currentSubroomId, message_id: msgId });
        }

        socket.on('message_deleted', (data) => {
            if (data.subroom_id === currentSubroomId) {
                document.querySelector(`.message[data-msg-id="${data.message_id}"]`)?.remove();
            }
        });

        function changeSubroom() {
            currentSubroomId = parseInt(subroomSelector.value);
            loadMessagesForSubroom();
        }

        function sendMessage() {
            const content = messageTextarea.value.trim();
            if (content && currentRoomId && currentSubroomId !== null) {
                socket.emit('send_message', { room_id: currentRoomId, subroom_id: currentSubroomId, message: content, is_image: false });
                messageTextarea.value = '';
                autoResizeTextarea();
                messageTextarea.focus();
            }
        }

        function sendImage(base64) {
            if (base64 && currentRoomId && currentSubroomId !== null) {
                socket.emit('send_message', { room_id: currentRoomId, subroom_id: currentSubroomId, message: base64, is_image: true });
            }
        }

        imageInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (!file) return;
            if (file.size > 5 * 1024 * 1024) {
                alert('Файл слишком большой (макс. 5 МБ)');
                imageInput.value = '';
                return;
            }
            const reader = new FileReader();
            reader.onload = (ev) => {
                sendImage(ev.target.result);
                imageInput.value = '';
            };
            reader.readAsDataURL(file);
        });

        messageTextarea.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        messageTextarea.addEventListener('input', autoResizeTextarea);

        function openManageRoles() {
            socket.emit('get_room_members', { room_id: currentRoomId });
        }

        socket.on('room_members', (members) => {
            const container = document.getElementById('roles-list');
            container.innerHTML = '';
            members.forEach(m => {
                const div = document.createElement('div');
                div.className = 'user-list-item';
                const roleBadge = `<span class="role-badge">${m.role}</span>`;
                div.innerHTML = `<span><strong>${escapeHtml(m.username)}</strong> ${roleBadge}</span>`;
                if ((currentRoomSettings.user_role === 'owner' || currentRoomSettings.user_role === 'admin') && m.user_id !== currentRoomSettings.creator_id) {
                    const select = document.createElement('select');
                    select.className = 'role-select';
                    select.innerHTML = `<option value="member" ${m.role === 'member' ? 'selected' : ''}>Пользователь</option><option value="moderator" ${m.role === 'moderator' ? 'selected' : ''}>Модератор</option><option value="admin" ${m.role === 'admin' ? 'selected' : ''}>Администратор</option>`;
                    select.onchange = () => socket.emit('update_user_role', { room_id: currentRoomId, target_user_id: m.user_id, new_role: select.value });
                    div.appendChild(select);
                }
                container.appendChild(div);
            });
            document.getElementById('manage-roles-modal').style.display = 'block';
        });

        function openCreateSubroom() {
            document.getElementById('subroom-name').value = '';
            document.getElementById('create-subroom-modal').style.display = 'block';
        }

        function submitCreateSubroom() {
            const name = document.getElementById('subroom-name').value.trim();
            if (!name) return alert('Введите название');
            socket.emit('create_subroom', { room_id: currentRoomId, name: name });
            closeModal('create-subroom-modal');
        }

        socket.on('subroom_created', (subroom) => {
            currentSubrooms.push(subroom);
            const option = document.createElement('option');
            option.value = subroom.id;
            option.textContent = subroom.name;
            subroomSelector.appendChild(option);
            subroomSelector.value = subroom.id;
            currentSubroomId = subroom.id;
            loadMessagesForSubroom();
        });

        function updateChatHeaderStatus() {
            if (currentRoomSettings?.type === 'dm' && currentDmPartner) {
                const status = userStatuses[currentDmPartner] || 'offline';
                const statusText = { online:'🟢 Онлайн', away:'🌙 Отошёл', dnd:'⛔ Не беспокоить', offline:'⚫ Не в сети' }[status];
                roomTitle.innerHTML = `${roomTitle.textContent.split(' — ')[0]} — <span style="font-size:0.8rem;">${statusText}</span>`;
            }
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function openCreateModal(type) {
            const modalTitle = document.getElementById('create-modal-title');
            const typeSelect = document.getElementById('create-type');
            if (type === 'channel') {
                modalTitle.textContent = 'Создать канал';
                typeSelect.value = 'channel';
            } else if (type === 'group') {
                modalTitle.textContent = 'Создать группу';
                typeSelect.value = 'group';
            }
            document.getElementById('create-name').value = '';
            document.getElementById('create-desc').value = '';
            document.getElementById('create-private').checked = false;
            document.getElementById('create-link').value = '';
            document.getElementById('create-modal').style.display = 'block';
        }

        function submitCreateRoom() {
            const name = document.getElementById('create-name').value.trim();
            if (!name) return alert('Введите название');
            socket.emit('create_room', {
                name,
                description: document.getElementById('create-desc').value.trim(),
                type: document.getElementById('create-type').value,
                is_private: document.getElementById('create-private').checked,
                invite_link: document.getElementById('create-link').value.trim()
            });
            closeModal('create-modal');
        }

        function openSearchModal() { document.getElementById('search-modal').style.display = 'block'; }
        function searchRooms() {
            const query = document.getElementById('search-query').value.trim();
            if (!query) return;
            socket.emit('search_rooms', { query });
        }
        socket.on('search_results', (results) => {
            const cont = document.getElementById('search-results');
            if (!results.length) { cont.innerHTML = '<p>Ничего не найдено</p>'; return; }
            cont.innerHTML = results.map(r => `<div style="display:flex; justify-content:space-between; padding:6px 0;"><span>${escapeHtml(r.name)} (${r.type})</span><button class="btn-primary" onclick="joinByInvite('${escapeHtml(r.invite_link)}')">Войти</button></div>`).join('');
        });
        function joinByInvite(link) {
            socket.emit('join_by_invite', { invite_link: link });
            closeModal('search-modal');
        }
        socket.on('join_success', () => {});
        socket.on('join_error', (msg) => alert(msg));

        function openUserSearchModal() { document.getElementById('user-search-modal').style.display = 'block'; }
        function searchUsers() {
            const query = document.getElementById('user-search-query').value.trim();
            if (!query) return;
            socket.emit('search_users', { query });
        }
        socket.on('user_search_results', (users) => {
            const cont = document.getElementById('user-search-results');
            if (!users.length) { cont.innerHTML = '<p>Ничего не найдено</p>'; return; }
            cont.innerHTML = users.map(u => `<div style="display:flex; justify-content:space-between; padding:6px 0;"><span>👤 ${escapeHtml(u.username)}</span><button class="btn-primary" onclick="startDM(${u.id})">Написать</button></div>`).join('');
        });
        function startDM(targetId) {
            socket.emit('create_dm', { target_user_id: targetId });
            closeModal('user-search-modal');
        }
        socket.on('dm_created', (room) => {
            if (currentView !== 'dm') switchView('dm');
            setTimeout(() => document.querySelector(`.room-item[data-room-id="${room.id}"]`)?.click(), 100);
        });

        function openRoomSettings() {
            if (!currentRoomSettings) return;
            document.getElementById('rs-name').value = currentRoomSettings.name || '';
            document.getElementById('rs-desc').value = currentRoomSettings.description || '';
            document.getElementById('rs-private').value = currentRoomSettings.is_private ? '1' : '0';
            document.getElementById('rs-link').value = currentRoomSettings.invite_link || '';
            document.getElementById('room-settings-modal').style.display = 'block';
        }
        function submitRoomSettings() {
            if (!currentRoomId) return;
            socket.emit('update_room', {
                room_id: currentRoomId,
                name: document.getElementById('rs-name').value.trim(),
                description: document.getElementById('rs-desc').value.trim(),
                is_private: document.getElementById('rs-private').value === '1',
                invite_link: document.getElementById('rs-link').value.trim()
            });
            closeModal('room-settings-modal');
        }

        function openProfileSettings() {
            document.getElementById('settings-modal').style.display = 'block';
            document.getElementById('profile-bio').value = document.getElementById('profile-bio').value || "{{ user_bio }}";
            const preview = document.getElementById('settings-avatar-preview');
            if (preview.src && !preview.src.includes('data:image') && preview.src !== window.location.href) {
                preview.style.display = 'block';
            } else {
                preview.src = "{{ user_avatar }}";
                if (preview.src && preview.src !== window.location.href) preview.style.display = 'block';
                else preview.style.display = 'none';
            }
            pendingAvatarBase64 = null;
            loadSoundSetting();
        }

        document.getElementById('settings-avatar-input').addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (!file) return;
            if (file.size > 2 * 1024 * 1024) {
                alert('Аватар не должен превышать 2 МБ');
                this.value = '';
                return;
            }
            if (!file.type.match('image/jpeg|image/png|image/gif')) {
                alert('Только JPG, PNG или GIF');
                this.value = '';
                return;
            }
            const reader = new FileReader();
            reader.onload = function(ev) {
                const preview = document.getElementById('settings-avatar-preview');
                preview.src = ev.target.result;
                preview.style.display = 'block';
                pendingAvatarBase64 = ev.target.result;
            };
            reader.readAsDataURL(file);
        });

        function switchSettingsTab(tab) {
            document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            if (tab === 'profile') {
                document.querySelector('.settings-tab:nth-child(1)').classList.add('active');
                document.getElementById('profile-tab').classList.add('active');
            } else if (tab === 'appearance') {
                document.querySelector('.settings-tab:nth-child(2)').classList.add('active');
                document.getElementById('appearance-tab').classList.add('active');
            } else if (tab === 'help') {
                document.querySelector('.settings-tab:nth-child(3)').classList.add('active');
                document.getElementById('help-tab').classList.add('active');
            } else if (tab === 'bug') {
                document.querySelector('.settings-tab:nth-child(4)').classList.add('active');
                document.getElementById('bug-tab').classList.add('active');
            }
        }

        function saveAllSettings() {
            const newTheme = document.getElementById('profile-theme').value;
            const fontSize = document.getElementById('font-size').value;
            const bio = document.getElementById('profile-bio').value.trim();
            const soundValue = document.getElementById('notification-sound').value;
            localStorage.setItem('notificationSound', soundValue);
            if (fontSize === 'small') document.body.style.fontSize = '12px';
            else if (fontSize === 'large') document.body.style.fontSize = '16px';
            else document.body.style.fontSize = '14px';
            socket.emit('update_profile', {
                avatar: pendingAvatarBase64,
                bio: bio,
                theme: newTheme
            });
            applyTheme(newTheme);
            if (pendingAvatarBase64) {
                document.getElementById('sidebar-avatar').src = pendingAvatarBase64;
            }
            closeModal('settings-modal');
        }

        function submitBug() {
            const subject = document.getElementById('bug-subject').value.trim();
            const description = document.getElementById('bug-description').value.trim();
            const email = document.getElementById('bug-email').value.trim();
            if (!subject || !description) {
                alert('Заполните тему и описание ошибки');
                return;
            }
            const bugReport = `Тема: ${subject}\\nОписание: ${description}\\nEmail: ${email || 'не указан'}\\nПользователь: ${username}\\nВремя: ${new Date().toLocaleString()}`;
            socket.emit('report_bug', { report: bugReport });
            alert('Спасибо! Сообщение отправлено разработчику.');
            document.getElementById('bug-subject').value = '';
            document.getElementById('bug-description').value = '';
            document.getElementById('bug-email').value = '';
        }

        function openUserProfile(targetUsername) {
            socket.emit('get_user_profile', { username: targetUsername });
            document.getElementById('user-profile-modal').style.display = 'block';
        }

        socket.on('user_profile', (data) => {
            const cont = document.getElementById('user-profile-content');
            if (data.error) { cont.innerHTML = `<p style="color:red;">${escapeHtml(data.error)}</p>`; return; }
            const statusText = { online:'🟢 Онлайн', away:'🌙 Отошёл', dnd:'⛔ Не беспокоить', offline:'⚫ Не в сети' }[userStatuses[data.username] || 'offline'];
            cont.innerHTML = `<p><strong>Имя:</strong> ${escapeHtml(data.username)}</p><p><strong>Статус:</strong> ${statusText}</p><p><strong>О себе:</strong> ${escapeHtml(data.bio || 'не указано')}</p>${data.avatar ? `<img src="${escapeHtml(data.avatar)}" style="max-width:100px; border-radius:50%; margin-top:10px;">` : ''}`;
        });

        socket.on('bug_received', () => {});

        function closeModal(id) { document.getElementById(id).style.display = 'none'; }
        window.onclick = function(e) { if (e.target.classList.contains('modal')) e.target.style.display = 'none'; };
        sendBtn.addEventListener('click', sendMessage);
        socket.on('connect', () => socket.emit('request_all_statuses'));
        socket.on('all_statuses', (statusMap) => { userStatuses = statusMap; document.getElementById('status-select').value = userStatuses[username] || 'online'; });
        socket.on('user_status_update', onStatusUpdate);
        socket.on('room_list_update', (rooms) => { if (currentView === 'rooms') updateList(rooms); });
        socket.on('dm_rooms_update', (rooms) => { if (currentView === 'dm') updateList(rooms); });
        socket.on('room_updated', () => { if (currentView === 'rooms') socket.emit('request_room_list'); });
        socket.on('avatar_update', (data) => {
            if (data.username === username) {
                document.getElementById('sidebar-avatar').src = data.avatar;
            }
        });

        const initialTheme = "{{ user_theme }}";
        applyTheme(initialTheme);
        switchView('rooms');
    </script>
</body>
</html>
'''

# ------------------ Flask routes ------------------
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('chat'))
    return redirect(url_for('login'))

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
        return "Неверное имя или пароль", 401
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/register', methods=['GET', 'POST'])
def register():
    global next_user_id
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users:
            return "Пользователь уже существует", 400
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
    my_rooms = []
    for room in rooms:
        if room.get('type') == 'dm':
            continue
        if session['user_id'] == room.get('creator_id') or session['user_id'] in room.get('members', []):
            my_rooms.append(room)
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

# ------------------ SocketIO events ------------------
def get_my_rooms(user_id):
    return [r for r in rooms if r.get('type') != 'dm' and (user_id == r.get('creator_id') or user_id in r.get('members', []))]

def get_dm_rooms(user_id):
    return [r for r in rooms if r.get('type') == 'dm' and user_id in r.get('members', [])]

@socketio.on('connect')
def handle_connect():
    user_id = session.get('user_id')
    if not user_id:
        return
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
    if not user_id:
        return
    participants = set()
    for room in rooms:
        if room.get('type') == 'dm':
            if user_id in room.get('members', []):
                participants.update(room.get('members', []))
        else:
            if user_id == room.get('creator_id') or user_id in room.get('members', []):
                if room.get('creator_id'):
                    participants.add(room['creator_id'])
                for m in room.get('members', []):
                    participants.add(m)
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
    if not user_id:
        return
    new_status = data.get('status')
    if new_status not in ('online', 'offline', 'away', 'dnd'):
        return
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
    if not user_id:
        return
    room_id = data['room_id']
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room:
        return
    if room.get('type') == 'dm':
        if user_id not in room.get('members', []):
            return
    else:
        if user_id != room.get('creator_id') and user_id not in room.get('members', []):
            return
    join_room(str(room_id))
    role = get_user_role(room, user_id)
    subrooms = room.get('subrooms', [{'id': 1, 'name': 'общий'}])
    emit('room_info', {
        'id': room['id'],
        'name': room['name'],
        'type': room.get('type'),
        'description': room.get('description', ''),
        'is_private': room.get('is_private', False),
        'invite_link': room.get('invite_link', ''),
        'creator_id': room.get('creator_id'),
        'user_role': role,
        'subrooms': subrooms
    })

@socketio.on('load_subroom_messages')
def handle_load_subroom_messages(data):
    user_id = session.get('user_id')
    if not user_id:
        return
    room_id = data['room_id']
    subroom_id = data['subroom_id']
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room:
        return
    if room.get('type') != 'dm' and user_id != room.get('creator_id') and user_id not in room.get('members', []):
        return
    msgs = messages.get(room_id, {}).get(subroom_id, [])[-50:]
    emit('subroom_history', msgs)

@socketio.on('load_older_messages')
def handle_load_older_messages(data):
    user_id = session.get('user_id')
    if not user_id:
        return
    room_id = data['room_id']
    subroom_id = data['subroom_id']
    before_msg_id = data.get('before_message_id')
    if not before_msg_id:
        return
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room:
        return
    if room.get('type') != 'dm' and user_id != room.get('creator_id') and user_id not in room.get('members', []):
        return
    all_msgs = messages.get(room_id, {}).get(subroom_id, [])
    idx = None
    for i, m in enumerate(all_msgs):
        if m['message_id'] == before_msg_id:
            idx = i
            break
    if idx is None or idx == 0:
        emit('older_messages', [])
        return
    start = max(0, idx - 50)
    older = all_msgs[start:idx]
    emit('older_messages', older)

@socketio.on('send_message')
def handle_send_message(data):
    user_id = session.get('user_id')
    if not user_id:
        return
    room_id = data['room_id']
    subroom_id = data['subroom_id']
    content = data['message'].strip()
    is_image = data.get('is_image', False)
    if not content:
        return
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room:
        return
    if room.get('type') == 'dm':
        if user_id not in room.get('members', []):
            return
    else:
        if user_id != room.get('creator_id') and user_id not in room.get('members', []):
            return
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
    if not user_id:
        return
    room_id = data['room_id']
    subroom_id = data['subroom_id']
    msg_id = data['message_id']
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room:
        return
    if not can_delete_message(room, user_id):
        return
    if room_id in messages and subroom_id in messages[room_id]:
        messages[room_id][subroom_id] = [m for m in messages[room_id][subroom_id] if m.get('message_id') != msg_id]
        save_data()
        emit('message_deleted', {'subroom_id': subroom_id, 'message_id': msg_id}, to=str(room_id))

@socketio.on('create_room')
def handle_create_room(data):
    user_id = session.get('user_id')
    if not user_id:
        return
    name = data['name'].strip()
    if not name:
        return
    link = data.get('invite_link') or uuid.uuid4().hex[:8]
    while any(r.get('invite_link') == link for r in rooms):
        link = uuid.uuid4().hex[:8]
    room = {
        'id': len(rooms) + 1,
        'name': name,
        'type': data.get('type', 'channel'),
        'creator_id': user_id,
        'is_private': data.get('is_private', False),
        'invite_link': link,
        'description': data.get('description', ''),
        'members': [user_id],
        'roles': {str(user_id): 'owner'},
        'subrooms': [{'id': 1, 'name': 'общий'}]
    }
    rooms.append(room)
    messages[room['id']] = {1: []}
    save_data()
    emit('room_list_update', get_my_rooms(user_id))

@socketio.on('update_room')
def handle_update_room(data):
    user_id = session.get('user_id')
    if not user_id:
        return
    room_id = data['room_id']
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room or room.get('creator_id') != user_id:
        return
    room['name'] = data.get('name', room['name'])
    room['description'] = data.get('description', room.get('description', ''))
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
    if not user_id:
        return
    query = data.get('query', '').lower()
    results = []
    for room in rooms:
        if room.get('type') == 'dm':
            continue
        if user_id == room.get('creator_id') or user_id in room.get('members', []):
            continue
        if (not room.get('is_private') and query in room['name'].lower()) or room.get('invite_link') == query:
            results.append({'name': room['name'], 'type': room['type'], 'invite_link': room['invite_link']})
    emit('search_results', results)

@socketio.on('join_by_invite')
def handle_join_by_invite(data):
    user_id = session.get('user_id')
    if not user_id:
        return
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
    if not user_id:
        return
    query = data.get('query', '').lower()
    results = []
    for uname, udata in users.items():
        if udata['id'] == user_id:
            continue
        if query in uname.lower():
            results.append({'username': uname, 'id': udata['id']})
    emit('user_search_results', results)

@socketio.on('create_dm')
def handle_create_dm(data):
    user_id = session.get('user_id')
    if not user_id:
        return
    target_id = data.get('target_user_id')
    if not target_id or target_id == user_id:
        return
    existing = next((r for r in rooms if r.get('type') == 'dm' and set(r.get('members', [])) == {user_id, target_id}), None)
    if existing:
        emit('dm_created', existing)
        emit('dm_rooms_update', get_dm_rooms(user_id))
        return
    target_uname = None
    for uname, udata in users.items():
        if udata['id'] == target_id:
            target_uname = uname
            break
    if not target_uname:
        return
    room_name = f"{session['username']} & {target_uname}"
    room = {
        'id': len(rooms) + 1,
        'name': room_name,
        'type': 'dm',
        'creator_id': user_id,
        'members': [user_id, target_id],
        'is_private': True,
        'subrooms': [{'id': 1, 'name': 'ЛС'}]
    }
    rooms.append(room)
    messages[room['id']] = {1: []}
    save_data()
    emit('dm_created', room)
    emit('dm_rooms_update', get_dm_rooms(user_id))

@socketio.on('get_room_members')
def handle_get_room_members(data):
    user_id = session.get('user_id')
    if not user_id:
        return
    room_id = data['room_id']
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room or room.get('type') == 'dm':
        return
    if user_id != room.get('creator_id') and user_id not in room.get('members', []):
        return
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
    if not user_id:
        return
    room_id = data['room_id']
    target_id = data['target_user_id']
    new_role = data['new_role']
    if new_role not in ('member', 'moderator', 'admin'):
        return
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room or room.get('type') == 'dm':
        return
    if not can_manage_roles(room, user_id):
        return
    if target_id == room.get('creator_id'):
        return
    if 'roles' not in room:
        room['roles'] = {}
    room['roles'][str(target_id)] = new_role
    save_data()
    emit('room_updated', to=str(room_id))

@socketio.on('create_subroom')
def handle_create_subroom(data):
    user_id = session.get('user_id')
    if not user_id:
        return
    room_id = data['room_id']
    name = data['name'].strip()
    if not name:
        return
    room = next((r for r in rooms if r['id'] == room_id), None)
    if not room or room.get('type') == 'dm':
        return
    if not can_create_subrooms(room, user_id):
        return
    if 'subrooms' not in room:
        room['subrooms'] = [{'id': 1, 'name': 'общий'}]
    new_id = max([sr['id'] for sr in room['subrooms']]) + 1 if room['subrooms'] else 1
    new_sub = {'id': new_id, 'name': name}
    room['subrooms'].append(new_sub)
    messages.setdefault(room_id, {})[new_id] = []
    save_data()
    emit('subroom_created', new_sub, to=str(room_id))

@socketio.on('update_profile')
def handle_update_profile(data):
    user_id = session.get('user_id')
    username = session.get('username')
    if not user_id or username not in users:
        return
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
    emit('user_profile', {
        'username': target_username,
        'bio': u.get('bio', ''),
        'avatar': u.get('avatar', '')
    })

@socketio.on('report_bug')
def handle_report_bug(data):
    report = data.get('report')
    if report:
        with open('bugs.log', 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().isoformat()} - {report}\n")
        emit('bug_received', {})

@socketio.on('leave')
def handle_leave(data):
    if data and 'room_id' in data:
        leave_room(str(data['room_id']))

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
