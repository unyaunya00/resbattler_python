## -*- coding: utf-8 -*-
from eventlet.semaphore import Semaphore
lock = Semaphore()

from flask import (
    Flask, Response, render_template, redirect,
    url_for, abort, jsonify, request, session
)
from flask_socketio import SocketIO, join_room, leave_room, emit, send

import os
import sys
import time
import uuid
import random
import json
from dotenv import load_dotenv

import openai
import sqlite3
import redis

from flask_session import Session
from flask_cors import CORS

r = redis.Redis(host='127.0.0.1', port=6379, db=10)

load_dotenv()
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = Flask(__name__, 
            template_folder='templates', 
            static_folder='static')

app.config['SECRET_KEY'] = 'resba1092popuser3332'
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_PERMANENT'] = True
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_KEY_PREFIX'] = 'flask_session:'
app.config['SESSION_REDIS'] = redis.from_url('redis://127.0.0.1:6379/10')
server_session = Session(app)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet", manage_session=False)
CORS(app, supports_credentials=True, responses={r"/*": {"origins": "*"}})
sys.path.insert(0, os.path.dirname(__file__))

RATING_ROOM = "rating_battlers_room"
UNRATE_ROOM = "unrate_battlers_room"
USER_SIDS = "user_sids"
USER_RATES = "user_rates"
RANKING_DATA = "ranking_data"

room_chats = {}

def make_db():
    connect = sqlite3.connect("resbattler.db")
    cursor = connect.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT,
            rate INTEGER,
            wincnt INTEGER,
            losecnt INTEGER,
            drawcnt INTEGER
        )
    ''')
    connect.commit()
    connect.close()

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/save_session', methods=['POST'])
def saveSession():
    username = request.form.get('username')
    session.permanent = True
    connect = sqlite3.connect("resbattler.db")
    cursor = connect.cursor()
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
        cursor.execute("INSERT INTO users (id, username, rate, wincnt, losecnt, drawcnt) VALUES (?, ?, ?, ?, ?, ?)", (session['user_id'], username, 1500, 0, 0, 0))
    else:
        cursor.execute("UPDATE users SET username = ? WHERE id = ?", (username, session['user_id']))
    connect.commit()
    connect.close()
    session['user_role'] = 'unrate'
    session['user_status'] = 'nothing'
    session.modified = True
    return redirect(url_for('selectBattle'))

@app.route('/resbattle', methods=['GET', 'POST'])
def selectBattle():
    connect = sqlite3.connect("resbattler.db")
    cursor = connect.cursor()
    cursor.execute("SELECT rate FROM users WHERE id = ?", (session['user_id'],))
    user_rate = cursor.fetchone()[0]
    connect.commit()
    connect.close()
    return render_template('resbattle.html', user_rate=user_rate)

@app.route('/add_battler', methods=['GET', 'POST'])
def addBattler():
    if not check_session():
        return redirect(url_for('index'))
    user_role = session['user_role']
    return redirect_user(user_role)

@app.route('/select_role', methods=['POST'])
def selectRole():
    session['user_status'] = 'waiting'
    user_id = session["user_id"]
    if not user_id:
        return redirect(url_for('index'))
    connect = sqlite3.connect("resbattler.db")
    cursor = connect.cursor()
    cursor.execute("SELECT rate FROM users WHERE id = ?", (user_id,))
    user_rate = cursor.fetchone()[0]
    connect.commit()
    connect.close()
    old_role = session.get('user_role')
    old_mydata = f'{user_id}:{user_rate}:{old_role}' if old_role else f'{user_id}:{user_rate}:none'
    role = request.form.get('role')
    if role in ['rating', 'unrate']:
        session['user_role'] = role
        with lock:
            rating_data = [d.decode().rsplit(':', 1)[0] for d in r.lrange(RATING_ROOM, 0, -1)]
            unrate_data = [d.decode().rsplit(':', 1)[0] for d in r.lrange(UNRATE_ROOM, 0, -1)]
            if old_mydata in rating_data:
                r.lrem(RATING_ROOM, 0, [d for d in r.lrange(RATING_ROOM, 0, -1) if d.decode().startswith(old_mydata)][0])
            elif old_mydata in unrate_data:
                r.lrem(UNRATE_ROOM, 0, [d for d in r.lrange(UNRATE_ROOM, 0, -1) if d.decode().startswith(old_mydata)][0])
        return redirect(url_for('addBattler'))
    else:
        return redirect(url_for('index'))

def check_session():
    if 'user_id' not in session or 'user_role' not in session:
        return False
    return True

@socketio.on('update_room')
def handle_update_room():
    connect = sqlite3.connect("resbattler.db")
    cursor = connect.cursor()
    user_id = session['user_id']
    cursor.execute("SELECT rate FROM users WHERE id = ?", (user_id,))
    user_rate = cursor.fetchone()[0]
    user_role = session['user_role']
    cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    user_name = cursor.fetchone()[0]
    connect.commit()
    connect.close()
    update_rooms(user_id, user_rate, user_role, user_name)

def update_rooms(user_id, user_rate, user_role, user_name):
    mydata = f'{user_id}:{user_rate}:{user_role}:{user_name}'
    with lock:
        if user_role == 'rating':
            r.rpush(RATING_ROOM, mydata)
        elif user_role == 'unrate':
            r.rpush(UNRATE_ROOM, mydata)
    rating_count = r.llen(RATING_ROOM)
    unrate_count = r.llen(UNRATE_ROOM)
    socketio.emit('update_room_count', {
        'rating_count': rating_count,
        'unrate_count': unrate_count
    })

def redirect_user(user_role):
    if user_role == 'rating':
        return redirect(url_for('waitrating'))
    elif user_role == 'unrate':
        return redirect(url_for('waitunrate'))
    else:
        return redirect(url_for('index'))

@app.route('/waitrating')
def waitrating():
    return render_template('waitrating.html')

@app.route('/waitunrate')
def waitunrate():
    return render_template('waitunrate.html')

global is_rating_matching
is_rating_matching = True
global is_unrate_matching
is_unrate_matching = True

def monitor_rating_matching():
    global is_rating_matching
    while is_rating_matching:
        if r.llen(RATING_ROOM) >= 2:
            with lock:
                if r.llen(RATING_ROOM) < 2:
                    pass
                else:
                    socketio.sleep(1)
                    is_rating_matching = False
                    player1 = r.lpop(RATING_ROOM)
                    player2 = r.lpop(RATING_ROOM)
                    if not (player1 and player2):
                        if player1: r.rpush(RATING_ROOM, player1) 
                        if player2: r.rpush(RATING_ROOM, player2)
                    socketio.start_background_task(matchRatingBattle, player1, player2)
        socketio.sleep(1)
def monitor_unrate_matching():
    global is_unrate_matching
    while is_unrate_matching:
        if r.llen(UNRATE_ROOM) >= 2:
            with lock:
                if r.llen(UNRATE_ROOM) < 2:
                    pass
                else:
                    socketio.sleep(1)
                    is_unrate_matching = False
                    playerA = r.lpop(UNRATE_ROOM)
                    playerB = r.lpop(UNRATE_ROOM)
                    if not (playerA and playerB):
                        if playerA: r.rpush(UNRATE_ROOM, playerA) 
                        if playerB: r.rpush(UNRATE_ROOM, playerB)
                    socketio.start_background_task(matchUnrateBattle, playerA, playerB)
        socketio.sleep(1)

def matchRatingBattle(player1, player2):
    global is_rating_matching
    try:
        if player1 and player2:
            player1_id = player1.decode().split(':')[0]
            player2_id = player2.decode().split(':')[0]
            room_id = f"room_{uuid.uuid4()}"
            player1_sid = r.hget(USER_SIDS, player1_id)
            player2_sid = r.hget(USER_SIDS, player2_id)

            if player1_sid:
                player1_sid = player1_sid.decode()
            if player2_sid:
                player2_sid = player2_sid.decode()

            if player1_sid and player2_sid:
                socketio.server.enter_room(player1_sid, room_id)
                socketio.server.enter_room(player2_sid, room_id)
                theme = generate_theme()
                room_chats[room_id] = [{"テーマ": theme}]
                socketio.emit('r_game_ready', {
                    'player1': player1.decode(), 
                    'player2': player2.decode(), 
                    'room': room_id,
                    'theme': theme,
                }, to=room_id)
    except Exception as e:
        rating_data = [d.decode().rsplit(':', 1)[0] for d in r.lrange(RATING_ROOM, 0, -1)]
        if player1 in rating_data:
            r.rpush(RATING_ROOM, player1)
        if player2 in rating_data:
            r.rpush(RATING_ROOM, player2)
    finally:
        is_rating_matching = True

def matchUnrateBattle(playerA, playerB):
    global is_unrate_matching
    try:
        if playerA and playerB:
            playerA_id = playerA.decode().split(':')[0]
            playerB_id = playerB.decode().split(':')[0]
            room_id = f"room_{uuid.uuid4()}"
            playerA_sid = r.hget(USER_SIDS, playerA_id)
            playerB_sid = r.hget(USER_SIDS, playerB_id)

            if playerA_sid:
                playerA_sid = playerA_sid.decode()
            if playerB_sid:
                playerB_sid = playerB_sid.decode()

            if playerA_sid and playerB_sid:
                socketio.server.enter_room(playerA_sid, room_id)
                socketio.server.enter_room(playerB_sid, room_id)
                theme = generate_theme()
                room_chats[room_id] = [{"テーマ": theme}]
                socketio.emit('u_game_ready', {
                    'playerA': playerA.decode(),
                    'playerB': playerB.decode(),
                    'room': room_id,
                    'theme': theme,
                }, to=room_id)
    except Exception as e:
        unrate_data = [d.decode().rsplit(':', 1)[0] for d in r.lrange(UNRATE_ROOM, 0, -1)]
        if playerA in unrate_data:
            r.rpush(UNRATE_ROOM, playerA)
        if playerB in unrate_data:
            r.rpush(UNRATE_ROOM, playerB)
    finally:
        is_unrate_matching = True

def generate_theme():
    themes = [
        "タバコの料金は上げるべき",
        "動物園の動物は野生の動物より幸せである",
        "人生に大切なものは，お金よりも愛である",
        "本よりもインターネットの方が情報を得るのに適している",
        "女性専用車両は廃止すべき",
        "レジ袋は有料にすべき",
        "死刑制度は廃止すべき",
        "大麻を合法化すべき",
        "学校で制服を廃止すべき",
        "AIが作る芸術は本物の芸術である",
        "年金制度は廃止すべき",
        "日本は移民をもっと受け入れるべき",
        "投票は義務化すべき",
        "宗教は人を幸せにする",
        "いじめをした生徒は退学にすべき",
        "学歴は重要である",
        "夫婦別姓を認めるべき",
        "男女平等はすでに達成されている",
        "日本のコンビニは24時間営業をやめるべき",
        "動物実験は禁止すべき",
        "格差は社会に必要である",
        "ベーシックインカムを導入すべき",
        "残業はなくすべき",
        "核兵器を全廃すべき",
        "仕事よりも家庭を優先すべき",
        "SNSは実名制にすべき",
        "高齢者の免許は一定年齢で強制返納すべき",
        "日本は核武装すべき",
        "公共の場での喫煙は全面禁止すべき",
        "学生に恋愛は必要ない要素である",
        "飲酒は20歳未満でも認めるべき",
        "終身雇用は維持すべき",
        "給与は年功序列ではなく完全実力主義にすべき",
        "日本の電車はもっと値上げすべき",
        "SNSの誹謗中傷は軽いものでも犯罪として厳罰化すべき",
        "ペットショップは廃止すべき",
        "結婚は制度として不要である",
        "学校のテストは廃止すべき",
        "宿題はなくすべき",
        "現金支払いは廃止すべき",
        "学校の授業は全てオンラインにすべき",
        "一夫多妻制（または一妻多夫制）を認めるべき",
        "医療は完全無料化すべき",
        "公務員の給与はもっと下げるべき",
        "人工知能が政治を運営すべき"
    ]
    return random.choice(themes)

def role_required(required_role):
    def decorator(f):
        def wrapper(*args, **kwargs):
            if 'user_role' not in session or session['user_role'] != required_role:
                return abort(403)
            return f(*args, **kwargs)
        wrapper.__name__ = f.__name__
        return wrapper
    return decorator

@app.route('/btlrating', methods=['GET'])
@role_required('rating')
def btlrating():
    session['user_status'] = 'inrp'
    room_id = request.args.get('room')
    return render_template('btlrating.html', to=room_id)

@app.route('/btlunrate', methods=['GET'])
@role_required('unrate')
def btlunrate():
    session['user_status'] = 'inup'
    room_id = request.args.get('room')
    return render_template('btlunrate.html', to=room_id)

rating_task_started = False
unrate_task_started = False
result_processing = {}

@socketio.on('connect')
def handle_connect():
    global rating_task_started, unrate_task_started
    if not rating_task_started:
        socketio.start_background_task(monitor_rating_matching)
        rating_task_started = True
    if not unrate_task_started:
        socketio.start_background_task(monitor_unrate_matching)
        unrate_task_started = True
    user_id = session.get('user_id')
    if user_id:
        current_sid = request.sid
        r.hset(USER_SIDS, user_id, current_sid)
    else:
        return redirect(url_for('index'))

@socketio.on('disconnect')
def handle_disconnect():
    connect = sqlite3.connect("resbattler.db")
    cursor = connect.cursor()
    user_id = session['user_id']
    cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    user_name = cursor.fetchone()[0]
    cursor.execute("SELECT rate FROM users WHERE id = ?", (user_id,))
    user_rate = cursor.fetchone()[0]
    user_role = session.get('user_role')
    user_status = session.get('user_status')
    old_mydata = f'{user_id}:{user_rate}:{user_role}' if user_role else f'{user_id}:{user_rate}:none'
    if user_id and user_status == 'waiting':
        r.hdel(USER_SIDS, user_id)
        with lock:
            rating_data = [d.decode().rsplit(':', 1)[0] for d in r.lrange(RATING_ROOM, 0, -1)]
            unrate_data = [d.decode().rsplit(':', 1)[0] for d in r.lrange(UNRATE_ROOM, 0, -1)]

            if old_mydata in rating_data:
                r.lrem(RATING_ROOM, 0, [d for d in r.lrange(RATING_ROOM, 0, -1) if d.decode().startswith(old_mydata)][0])
            elif old_mydata in unrate_data:
                r.lrem(UNRATE_ROOM, 0, [d for d in r.lrange(UNRATE_ROOM, 0, -1) if d.decode().startswith(old_mydata)][0])

            rating_count = r.llen(RATING_ROOM)
            unrate_count = r.llen(UNRATE_ROOM)
            socketio.emit('update_room_count', {
                'rating_count': rating_count,
                'unrate_count': unrate_count
            })
    connect.commit()
    connect.close()

@socketio.on('join') 
def on_join(data):
    room_id = data['room_id'] 
    join_room(room_id)

@socketio.on('leave')
def on_leave(data):
    room_id = data['room_id']
    leave_room(room_id)

@socketio.on('new_message1')
def handle_new_message1(data):
    text1 = data['text']
    room_id = data['room_id']
    room_chats[room_id].append({"role": "player1", "content": text1})
    socketio.emit('player1_message', {
        'data': text1,
        'room_id': room_id,
    }, to=room_id)

@socketio.on('new_message2')
def handle_new_message2(data):
    text2 = data['text']
    room_id = data['room_id']
    room_chats[room_id].append({"role": "player2", "content": text2})
    socketio.emit('player2_message', {
        'data': text2,
        'room_id': room_id,
    }, to=room_id)

@socketio.on('finishedRating')
def handle1_points(data):
    user_id = session.get('user_id')
    user_status = session.get('user_status')
    room_id = data['room_id']
    if room_id in result_processing and result_processing[room_id]:
        return
    result_processing[room_id] = True
    try:
        p1_id = data['p1_id']
        p2_id = data['p2_id']
        p1_name = data['p1_name']
        p2_name = data['p2_name']
        p1_rate = data['p1_rate']
        p2_rate = data['p2_rate']
        room_id = data['room_id']
        chat_history = room_chats[room_id]
        # ここにAIの判定
        messages=[{"role": "system", "content": """
            あなたは与えられたレスバトルの採点を行うアシスタントです。
            次の質問には**必ず有効なJSON形式のみ**で回答してください。
            ※絶対に日本語の説明文やコメントを入れないでください。
            ※すべてのキーと文字列は必ずダブルクオート " で囲んでください。
            ※点数は必ず0~10点でつけてください。
            【出力フォーマット】：
                [
                    {"player1": {"lp": 数値, "rp": 数値, "ap": 数値}},
                    {"player2": {"lp": 数値, "rp": 数値, "ap": 数値}}
                ]
            """},
            {"role": "user", "content": [
                {"type": "text", "text": data.get("content", f'{chat_history}')}
            ]}
        ]
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=100,
            temperature=0.7
        )
        response_text = response.choices[0].message.content
        response_data = json.loads(response_text)
        p1Scores = response_data[0]["player1"]
        p2Scores = response_data[1]["player2"]
        p1sumPoint = p1Scores.get('lp') + p1Scores.get('rp') + p1Scores.get('ap')
        p2sumPoint = p2Scores.get('lp') + p2Scores.get('rp') + p2Scores.get('ap')
        if p1sumPoint > p2sumPoint:
            win_id = p1_id
            lose_id = p2_id
            p1_rate, p2_rate = simple_elo_rate_1vs1(p1_rate, p2_rate)
        elif p1sumPoint < p2sumPoint:
            win_id = p2_id
            lose_id = p1_id
            p2_rate, p1_rate = simple_elo_rate_1vs1(p2_rate, p1_rate)
        elif p1sumPoint == p2sumPoint:
            win_id = 'draw'
            lose_id = 'draw'
            p1_rate = p1_rate
            p2_rate = p2_rate
        connect = sqlite3.connect("resbattler.db")
        cursor = connect.cursor()
        if p1sumPoint > p2sumPoint:
            win_id = p1_id
            lose_id = p2_id
            if user_id == win_id:
                cursor.execute("SELECT wincnt FROM users WHERE id = ?", (user_id,))
                user_wincnt = cursor.fetchone()[0]
                cursor.execute("UPDATE users SET rate = ? WHERE id = ?", (f'{p1_rate}', f'{user_id}'))
                cursor.execute("UPDATE users SET wincnt = ? WHERE id = ?", (f'{int(user_wincnt) + 1}', f'{user_id}'))
                update_ranking_data(p1_id, p1_name, p1_rate)
            elif user_id == lose_id:
                cursor.execute("SELECT losecnt FROM users WHERE id = ?", (user_id,))
                user_losecnt = cursor.fetchone()[0]
                cursor.execute("UPDATE users SET rate = ? WHERE id = ?", (f'{p2_rate}', f'{user_id}'))
                cursor.execute("UPDATE users SET losecnt = ? WHERE id = ?", (f'{int(user_losecnt) + 1}', f'{user_id}'))
                update_ranking_data(p2_id, p2_name, p2_rate)
        elif p1sumPoint < p2sumPoint:
            win_id = p2_id
            lose_id = p1_id
            if user_id == win_id:
                cursor.execute("SELECT wincnt FROM users WHERE id = ?", (user_id,))
                user_wincnt = cursor.fetchone()[0]
                cursor.execute("UPDATE users SET rate = ? WHERE id = ?", (f'{p2_rate}', f'{user_id}'))
                cursor.execute("UPDATE users SET wincnt = ? WHERE id = ?", (f'{int(user_wincnt) + 1}', f'{user_id}'))
                update_ranking_data(p2_id, p2_name, p2_rate)
            elif user_id == lose_id:
                cursor.execute("SELECT losecnt FROM users WHERE id = ?", (user_id,))
                user_losecnt = cursor.fetchone()[0]
                cursor.execute("UPDATE users SET rate = ? WHERE id = ?", (f'{p1_rate}', f'{user_id}'))
                cursor.execute("UPDATE users SET losecnt = ? WHERE id = ?", (f'{int(user_losecnt) + 1}', f'{user_id}'))
                update_ranking_data(p1_id, p1_name, p1_rate)
        elif p1sumPoint == p2sumPoint:
            if user_id == p1_id:
                cursor.execute("SELECT drawcnt FROM users WHERE id = ?", (user_id,))
                user_drawcnt = cursor.fetchone()[0]
                cursor.execute("UPDATE users SET rate = ? WHERE id = ?", (f'{p1_rate}', f'{user_id}'))
                cursor.execute("UPDATE users SET drawcnt = ? WHERE id = ?", (f'{int(user_drawcnt) + 1}', f'{user_id}'))
                update_ranking_data(p1_id, p1_name, p1_rate)
            elif user_id == p2_id:
                cursor.execute("SELECT drawcnt FROM users WHERE id = ?", (user_id,))
                user_drawcnt = cursor.fetchone()[0]
                cursor.execute("UPDATE users SET rate = ? WHERE id = ?", (f'{p2_rate}', f'{user_id}'))
                cursor.execute("UPDATE users SET drawcnt = ? WHERE id = ?", (f'{int(user_drawcnt) + 1}', f'{user_id}'))
                update_ranking_data(p2_id, p2_name, p2_rate)
        connect.commit()
        connect.close()
        if user_id and user_status == 'inrp':
            socketio.emit('displayRatingResult', {
                'p1Scores': p1Scores,
                'p2Scores': p2Scores,
                'win_id': win_id,
                'lose_id': lose_id,
                'p1_name' : p1_name,
                'p2_name' : p2_name,
                'p1_rate': p1_rate,
                'p2_rate': p2_rate,
            }, to=room_id)
    finally:
        session["chat_history"] = []
        socketio.start_background_task(lambda: reset_processing_flag(room_id))

@socketio.on('finishedUnrate')
def handle2_points(data):
    user_id = session.get('user_id')
    user_status = session.get('user_status')
    room_id = data['room_id']
    if room_id in result_processing and result_processing[room_id]:
        return
    result_processing[room_id] = True
    try:
        p1_id = data['p1_id']
        p2_id = data['p2_id']
        p1_name = data['p1_name']
        p2_name = data['p2_name']
        room_id = data['room_id']
        chat_history = room_chats[room_id]
        # ここにAIの判定
        messages=[{"role": "system", "content": """
            あなたは与えられたレスバトルの採点を行うアシスタントです。
            次の質問には**必ず有効なJSON形式のみ**で回答してください。
            ※絶対に日本語の説明文やコメントを入れないでください。
            ※すべてのキーと文字列は必ずダブルクオート " で囲んでください。
            ※点数は必ず0~10点でつけてください。
            【出力フォーマット】：
                [
                    {"player1": {"lp": 数値, "rp": 数値, "ap": 数値}},
                    {"player2": {"lp": 数値, "rp": 数値, "ap": 数値}}
                ]
            """},
            {"role": "user", "content": [
                {"type": "text", "text": data.get("content", f'{chat_history}')}
            ]}
        ]
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=100,
            temperature=0.7
        )
        response_text = response.choices[0].message.content
        response_data = json.loads(response_text)
        p1Scores = response_data[0]["player1"]
        p2Scores = response_data[1]["player2"]
        p1sumPoint = p1Scores.get('lp') + p1Scores.get('rp') + p1Scores.get('ap')
        p2sumPoint = p2Scores.get('lp') + p2Scores.get('rp') + p2Scores.get('ap')
        if p1sumPoint > p2sumPoint:
            win_id = p1_id
            lose_id = p2_id
        elif p1sumPoint < p2sumPoint:
            win_id = p2_id
            lose_id = p1_id
        elif p1sumPoint == p2sumPoint:
            win_id = 'draw'
            lose_id = 'draw'
        if user_id and user_status == 'inup':
            socketio.emit('displayUnrateResult', {
                'p1Scores': p1Scores, 
                'p2Scores': p2Scores,
                'win_id': win_id,
                'lose_id': lose_id,
                'p1_name' : p1_name,
                'p2_name' : p2_name,
            }, to=room_id)
    finally:
        session["chat_history"] = []
        socketio.start_background_task(lambda: reset_processing_flag(room_id))

def reset_processing_flag(room_id):
    socketio.sleep(10) 
    if room_id in result_processing:
        del result_processing[room_id]

def simple_elo_rate_1vs1(r1, r2):
    r1 = float(r1)
    r2 = float(r2)
    x = (r1 - r2) / 400
    new_r1 = r1 + 10 * (-(x / 2) + 1.5)
    new_r2 = r2 - 10 * (-(x / 2) + 1.5)
    new_r1 = int(new_r1)
    new_r2 = int(new_r2)
    return new_r1, new_r2

def update_ranking_data(user_id, user_name, user_rate):
    if lock.acquire(timeout=5):
        try:
            ranking_data = f'{user_id}:{user_name}:{user_rate}'
            if ranking_data and user_id in [d.decode().split(':')[0] for d in r.lrange(RANKING_DATA, 0, -1)]:
                r.lrem(RANKING_DATA, 0, [d for d in r.lrange(RANKING_DATA, 0, -1) if d.decode().startswith(user_id)][0])
            if ranking_data and user_id not in [d.decode().split(':')[0] for d in r.lrange(RANKING_DATA, 0, -1)]:
                r.rpush(RANKING_DATA, ranking_data)
        finally:
            lock.release()
    else:
        return

@socketio.on('getRankingData')
def handle_getRankingData():
    ranking_datas = [d.decode() for d in r.lrange(RANKING_DATA, 0, -1)]
    parsed_data = []
    for data in ranking_datas:
        user_id, user_name, user_rate = data.split(':')
        parsed_data.append(f'{user_id}:{user_name}:{user_rate}')
    sorted_data = sorted(parsed_data, key=lambda x: x.split(':')[2], reverse=True)
    top100_data = sorted_data[:100]
    socketio.emit('displayRankingData', top100_data, room=request.sid)

@socketio.on('init_room_count')
def handle_roomCnt():
    rating_count = r.llen(RATING_ROOM)
    unrate_count = r.llen(UNRATE_ROOM)
    socketio.emit('update_room_count', {
        'rating_count': rating_count,
        'unrate_count': unrate_count
    })

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
