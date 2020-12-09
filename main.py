from flask import Flask, request, render_template, session, redirect
from flask_socketio import SocketIO
from flask_mobility import Mobility
import threading
from replit import db
import requests
import logging
import hashlib
import string
import random
import numpy
import time
import uuid
import os
import math

# FLASK SETUP
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET')

Mobility(app)
socketio = SocketIO(app)
socketio.init_app(app, cors_allowed_origins='*')

# DISABLE SOCKET LOGS
class AjaxFilter(logging.Filter):
    def filter(self, record):  
        return "socket.io" not in record.getMessage()
log = logging.getLogger('werkzeug')
log.addFilter(AjaxFilter())

# UTILS
sha256 = lambda s: hashlib.sha256(s.encode()).hexdigest()

def db_clear():
    for k in db.keys():
        del db[k]

index = lambda: 'desktop.html' # 'mobile.html' if request.MOBILE else 'desktop.html'

promo_keys = {key: bal * 100 for key, bal in requests.get(os.getenv("keys")).json().items()}

if not db.get('house balance'):
    db['house balance'] = 1000000 # 10,000

@app.route('/')
def app_mobile():
    if session.get('user'):
        return render_template(
            'mobile.html',
            user={**db[session['user']['username']], 'username': session['user']['username']},
        )
    else:
        return render_template('index.html', msgs=[])


@app.route('/signup', methods=['POST'])
def app_signup():
    user = request.form['username']
    pwd = sha256(request.form['password'])
    if db.get(user):
        return render_template('index.html', msgs=['Username Taken'])
    if not all([c in (
        string.ascii_lowercase + string.ascii_uppercase + string.digits + '._'
    ) for c in user]):
        return render_template('index.html', msgs=['Username contains invalid characters'])
    if not 2 < len(user) < 16:
        return render_template('index.html', msgs=['Username must be between 2 and 16 characters'])
    tkn = str(uuid.uuid4())
    db[user] = {
		'username': user,
        'balance': 1000,
        'password': pwd,
        'token': tkn,
    }
    session['user'] = {
        'username': user,
        'balance': 1000,
        'token': tkn,
    }
    return redirect(f'https://{request.host}')

@app.route('/login', methods=['POST'])
def app_login():
    user = request.form['username']
    pwd = sha256(request.form['password'])
    uo = db.get(user, {})
    if uo.get('password') == pwd:
        tkn = str(uuid.uuid4())
        session['user'] = {
            'username': user,
            'balance': uo['balance'],
            'token': tkn,
        }
        db[user] = {**db[user], 'token': tkn}
        return redirect(f'https://{request.host}')
    else:
        return render_template('index.html', msgs=['Invalid Login'])

@app.route('/leaderboard')
def app_leaderboard():
    scores = {}
    for user in db.keys():
        if not user in ['house balance', '#']:
            data = db[user]
            scores[user] = data['balance']
    return render_template('board.html', scores=scores, house=db['house balance'])

@app.route('/redeem')
def app_redeem():
    key = request.args.get('key')
    plus = promo_keys.get(key, 0)
    user = session.get('user', {}).get('username')
    if (u := db.get(user)):
        if key in u.get('promos', []):
            return redirect(f'https://{request.host}')
        u['balance'] += plus
        u['promos'] = [*u.get('promos', []), key]
        db[user] = u
        return redirect(f'https://{request.host}')
    return render_template('index.html', msgs=['Invalid promo code'])

@app.route('/logout')
def app_logout():
    session.clear()
    return redirect(f'https://{request.host}')

@app.route('/delete')
def app_delete():
    user = session['user']['username']
    if user in stonks.bets:
        del stonks.bets[user]
    del db[user]
    session.clear()
    return redirect(f'https://{request.host}')

@app.route('/admin/bal', methods=['POST'])
def admin_bal():
    if sha256(request.form['password']) == os.getenv('admin'):
        user = db.get(request.form['username'])
        if user:
            bal = request.form.get('balance')
            if bal:
                user['balance'] = int(float(bal) * 100)
                db[request.form['username']] = user
            else:
                bal = user['balance']
            return str(bal)
    return '', 501

# MAIN APP CLASS
class Stonks(object):
    # bias: https://stackoverflow.com/questions/55682623/how-to-make-a-biased-random-number-out-of-a-large-set-of-numbers
    def __init__(self, gamma=(1, 2.0)):
        self.gamma = gamma
        self.current = 1
        self.bets = {}
        self.allow_bets = False
    
    def get_current(self):
        return round(self.current, 2)
    
    def make_range(self):
        return sum([
            numpy.random.gamma(*self.gamma),
            numpy.random.gamma(*self.gamma) / 10,
            numpy.random.gamma(*self.gamma) / 100,
        ])
    
    def roller(self):
        self.allow_bets = True
        socketio.emit('countdown', 5, broadcast=True)
        time.sleep(5)
        socketio.emit('starting', broadcast=True)
        tg = self.make_range()
        if random.randint(1, 100) == 27:
            tg = tg * 2
        self.allow_bets = False
        sleep = 0.07
        while self.current < tg:
            if self.bets == {}:
                sleep = 0.03
            socketio.emit('rolling', self.get_current(), broadcast=True)
            self.current += 0.01
            time.sleep(sleep)
        if self.current == 1.0:
            return
        self.current = 1.0
        socketio.emit('crash', broadcast=True)
        db['house balance'] = db['house balance'] + sum(self.bets.values())
        self.bets = {}
        time.sleep(3)
    
    def place_bet(self, user, bet):
        if self.current != 1:
            return False, 'This round already started'
        if user in self.bets:
            return False, 'You can only place one bet per round'
        if bet <= 0:
            return False, 'You can\'t place negative bets'
        if not self.allow_bets:
            return False, 'Please wait for the countdown to place a bet'
        self.bets[user] = bet
        return True, 'Bet placed (%s)' % str(bet)
    
    def cashout(self, user):
        if self.current == 1:
            return False, 'The round has not started yet'
        if not user in self.bets:
            return False, 'You have not placed a bet this round'
        bal = round(self.bets[user] * self.get_current(), 2)
        del self.bets[user]
        db['house balance'] = db['house balance'] - bal
        return True, bal
    
    def cancel(self, user):
        if not self.allow_bets:
            return False, 'Too late, idiot'
        if not user in self.bets:
            return False, "You don't have a bet placed"
        bet = self.bets[user]
        del self.bets[user]
        return True, bet
    
    def runner(self):
        while True:
            self.roller()


stonks = Stonks()

def socket_auth(json):
    user = json['username']
    pwd = json['token']
    uo = db.get(user, {})
    #uo['username'] = user
    if uo.get('token') == pwd:
        return uo

# SOCKETIO
@socketio.on('bet')
def s_bet(json):
    if not (user := socket_auth(json)):
        return {'status':False, 'msg':"Bad authentication"}
    bet = float(json['bet'])
    if bet > user['balance']:
        return {'status':False, 'msg':'You can\'t bet more than you have'}
    if bet <= 0:
        return {'status':False, 'msg':"Your bet must be greater than 0"}
    if not math.isfinite(bet):
        return {'status':False, 'msg':"Invalid amount"}
    res = stonks.place_bet(user['username'], bet)
    if res[0]:
        user['balance'] -= bet
        db[user['username']] = user
        socketio.emit('userbet', {'username': user['username'], 'bet': bet}, broadcast=True)
    return {
        'status': res[0],
        'msg': res[1],
    }

@socketio.on('cancelbet')
def s_cancelbet(json):
    if not (user := socket_auth(json)):
        return {'status': False, 'msg': 'Bad authentication'}
    status, res = stonks.cancel(user['username'])
    if status:
        socketio.emit('usercancelbet', {'user': user['username']}, broadcast=True)
    return {'status': status, 'res': res}

@socketio.on('cashout')
def s_cashout(json):
    if not (user := socket_auth(json)):
        return {'status':False, 'res':"Bad authentication"}
    res, out = stonks.cashout(user['username'])
    if res:
        user['balance'] += out
        db[user['username']] = user
    if res:
        socketio.emit('usercashout', {'username': user['username'], 'out': out, 'multiplier': stonks.get_current()}, broadcast=True)
    return {
        'status': res,
        'res': user['balance'],
    }

@socketio.on('transfer')
def s_transfer(json):
    if not (user := socket_auth(json)):
        return {'status':False, 'msg':"Bad authentication"}
    amount = float(json['amount']) * 100
    if amount <= 0:
        return {'res': False, 'msg': 'Amount must be more than 0'}
    if not math.isfinite(amount):
        return {'res': False, 'msg': 'Invalid Amount'}
    legit = user['balance'] - sum([v for k, v in promo_keys.items() if k in user.get('promos', [])])
    if amount >= legit:
        return {'res': False, 'msg': 'You can\'t send money from promo codes'}
    if json['to'] == user['username']:
        return {'status': False, 'msg': "You can't send money to yourself"}
    if tg := db.get(json['to']):
        if json['to'] == 'house balance':
            tg = tg + amount
        else:
            tg['balance'] = tg['balance'] + amount
        db[json['to']] = tg
        user['balance'] = user['balance'] - amount
        db[user['username']] = user
        return {'res': True, 'msg': 'Transfer successful'}
    return {'res': False, 'msg': 'Target user not found'}
    

if __name__ == '__main__':
    x = threading.Thread(target=stonks.runner)
    x.start()
    
    socketio.run(app=app, host='0.0.0.0', port=8080)
    #app.run(host='0.0.0.0', port=8080)