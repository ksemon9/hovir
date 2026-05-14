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
# Путь к папке с данными (можно переопределить через переменную окружения)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(BASE_DIR, 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, 'data.json')

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask и SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'supersecretkey')
socketio = SocketIO(app, async_mode='threading')

# ------------------ ЗАГРУЗКА / СОХРАНЕНИЕ ДАННЫХ ------------------
def load_data():
    """Загружает данные из JSON-файла, возвращает кортеж (users, next_user_id, rooms, messages)."""
    if not os.path.exists(DATA_FILE):
        logger.info("Файл данных не найден, создаём новую базу")
        return {}, 1, [], {}

    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    users = data.get('users', {})
    next_user_id = data.get('next_user_id', 1)
    rooms = data.get('rooms', [])
    raw_messages = data.get('messages', {})

    # Конвертируем ключи комнат и подкомнат в int
    converted_messages = {}
    for room_id_str, msgs in raw_messages.items():
        room_id = int(room_id_str)
        if isinstance(msgs, list):
            converted_messages[room_id] = {1: msgs}
        elif isinstance(msgs, dict):
            converted_messages[room_id] = {int(sub_id): sub_msgs for sub_id, sub_msgs in msgs.items()}
        else:
            converted_messages[room_id] = {1: []}

    # Нормализация комнат (добавляем недостающие поля)
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
    """Сохраняет текущее состояние в JSON-файл."""
    data = {
        'users': users,
        'next_user_id': next_user_id,
        'rooms': rooms,
        'messages': {str(k): {str(sk): sm for sk, sm in v.items()} for k, v in messages.items()}
    }
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.debug("Данные сохранены")

# Инициализация глобальных структур
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
    """Оповещает все комнаты, где есть пользователь, об изменении его статуса."""
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
    logger.info(f"Получен сигнал {signum}, сохраняем данные...")
    save_data()
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown_signal_handler)
signal.signal(signal.SIGINT, shutdown_signal_handler)

# ------------------ HTML ШАБЛОНЫ ------------------
# (шаблоны остались без изменений, кроме замены ICQ на Hovir)
# Для краткости они здесь не переписаны, так как их содержимое не менялось.
# В реальном файле они должны быть как в предыдущем ответе.
# Пожалуйста, используйте шаблоны из предыдущего сообщения, они корректны.
LOGIN_TEMPLATE = '''...'''  # см. предыдущий код
REGISTER_TEMPLATE = '''...'''
CHAT_TEMPLATE = '''...'''

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

# ------------------ SOCKETIO СОБЫТИЯ ------------------
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
    # Проверка прав
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
    idx = next((i for i, m in enumerate(all_msgs) if m['message_id'] == before_msg_id), None)
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
    # Ограничиваем историю 500 сообщениями на подкомнату
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
    if not room or not can_delete_message(room, user_id):
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
    results = [{'username': uname, 'id': udata['id']} for uname, udata in users.items()
               if udata['id'] != user_id and query in uname.lower()]
    emit('user_search_results', results)

@socketio.on('create_dm')
def handle_create_dm(data):
    user_id = session.get('user_id')
    if not user_id:
        return
    target_id = data.get('target_user_id')
    if not target_id or target_id == user_id:
        return
    # Проверяем, существует ли уже диалог
    existing = next((r for r in rooms if r.get('type') == 'dm' and set(r.get('members', [])) == {user_id, target_id}), None)
    if existing:
        emit('dm_created', existing)
        emit('dm_rooms_update', get_dm_rooms(user_id))
        return
    target_uname = next((uname for uname, udata in users.items() if udata['id'] == target_id), None)
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
    if not room or room.get('type') == 'dm' or not can_manage_roles(room, user_id):
        return
    if target_id == room.get('creator_id'):
        return
    room.setdefault('roles', {})[str(target_id)] = new_role
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
    if not room or room.get('type') == 'dm' or not can_create_subrooms(room, user_id):
        return
    room.setdefault('subrooms', [{'id': 1, 'name': 'общий'}])
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
