from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import os

app = Flask(__name__)

# 🔐 SECURITY (nur über Environment Variablen!)
app.secret_key = os.environ.get('SECRET_KEY')

# 🗄️ DB (für Produktion besser später PostgreSQL)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'sqlite:///budget.db'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


# =========================
# MODELS
# =========================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)  # hashed!


class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=False)


class Entry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    month = db.Column(db.String(7), nullable=False)
    desc = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payer = db.Column(db.String(80), nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    split_mode = db.Column(db.String(20), default='equal')
    share_a = db.Column(db.Float, default=50)
    share_b = db.Column(db.Float, default=50)
    rhythm = db.Column(db.String(20), default='variable')
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# =========================
# INIT DB (nur einmal beim Start!)
# =========================

with app.app_context():
    db.create_all()

    # Demo-User nur wenn keine existieren
    if not User.query.first():
        db.session.add(User(
            username='Flo',
            password=generate_password_hash('1234')
        ))
        db.session.add(User(
            username='justine',
            password=generate_password_hash('4321')
        ))

        defaults = {
            'name_a': 'Person 1',
            'name_b': 'Person 2',
            'income_a': '0',
            'income_b': '0',
            'goal': '0',
            'start_balance': '0'
        }

        for k, v in defaults.items():
            if not Setting.query.filter_by(key=k).first():
                db.session.add(Setting(key=k, value=v))

        db.session.commit()


# =========================
# HELPERS
# =========================

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper


def get_setting(key, default=''):
    s = Setting.query.filter_by(key=key).first()
    return s.value if s else default


def set_setting(key, value):
    s = Setting.query.filter_by(key=key).first()
    if not s:
        db.session.add(Setting(key=key, value=str(value)))
    else:
        s.value = str(value)


# =========================
# AUTH
# =========================

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None

    if request.method == 'POST':
        u = User.query.filter_by(username=request.form['username']).first()

        if u and check_password_hash(u.password, request.form['password']):
            session['user_id'] = u.id
            session['username'] = u.username
            return redirect(url_for('index'))

        error = 'Falsche Daten'

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# =========================
# MAIN APP
# =========================

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'save_settings':
            for k in ['name_a', 'name_b', 'income_a', 'income_b', 'goal', 'start_balance']:
                set_setting(k, request.form.get(k, '0'))
            db.session.commit()

        elif action == 'add_entry':
            db.session.add(Entry(
                month=request.form['month'],
                desc=request.form['desc'],
                category=request.form['category'],
                amount=float(request.form['amount']),
                payer=request.form['payer'],
                kind=request.form['kind'],
                split_mode=request.form.get('split_mode', 'equal'),
                share_a=float(request.form.get('share_a', 50)),
                share_b=float(request.form.get('share_b', 50)),
                rhythm=request.form.get('rhythm', 'variable'),
                note=request.form.get('note', '')
            ))
            db.session.commit()

        return redirect(url_for('index'))

    month = request.args.get('month', datetime.now().strftime('%Y-%m'))

    entries = Entry.query.filter_by(month=month).order_by(Entry.created_at.desc()).all()

    name_a = get_setting('name_a', 'Person 1')
    name_b = get_setting('name_b', 'Person 2')

    income_a = float(get_setting('income_a', '0') or 0)
    income_b = float(get_setting('income_b', '0') or 0)
    goal = float(get_setting('goal', '0') or 0)
    start_balance = float(get_setting('start_balance', '0') or 0)

    total_all = sum(e.amount for e in entries)
    paid_a = sum(e.amount for e in entries if e.payer == 'A')
    paid_b = sum(e.amount for e in entries if e.payer == 'B')

    owed_a = 0
    owed_b = 0

    for e in entries:
        if e.kind == 'private':
            if e.payer == 'A':
                owed_a += e.amount
            else:
                owed_b += e.amount
        elif e.split_mode == 'percent':
            owed_a += e.amount * (e.share_a / 100)
            owed_b += e.amount - owed_a
        elif e.split_mode == 'custom':
            owed_a += e.share_a
            owed_b += e.amount - e.share_a
        else:
            owed_a += e.amount / 2
            owed_b += e.amount / 2

    diff = paid_a - owed_a

    if abs(diff) < 0.01:
        settlement = 'Ausgeglichen'
    else:
        settlement = f"{name_b} schuldet {name_a} {abs(diff):.2f} €" if diff > 0 else f"{name_a} schuldet {name_b} {abs(diff):.2f} €"

    return render_template(
        'index.html',
        username=session.get('username'),
        month=month,
        entries=entries,
        name_a=name_a,
        name_b=name_b,
        income_a=income_a,
        income_b=income_b,
        goal=goal,
        start_balance=start_balance,
        total_all=total_all,
        paid_a=paid_a,
        paid_b=paid_b,
        owed_a=owed_a,
        owed_b=owed_b,
        settlement=settlement,
        progress=max(0, start_balance + income_a + income_b - total_all)
    )


# =========================
# DELETE
# =========================

@app.route('/delete/<int:entry_id>', methods=['POST'])
@login_required
def delete_entry(entry_id):
    e = Entry.query.get_or_404(entry_id)
    db.session.delete(e)
    db.session.commit()
    return redirect(url_for('index'))


# =========================
# API
# =========================

@app.route('/api/entries')
@login_required
def api_entries():
    return jsonify([
        {
            "id": e.id,
            "month": e.month,
            "desc": e.desc,
            "category": e.category,
            "amount": e.amount,
            "payer": e.payer,
            "kind": e.kind,
            "split_mode": e.split_mode,
            "share_a": e.share_a,
            "share_b": e.share_b,
            "rhythm": e.rhythm,
            "note": e.note
        }
        for e in Entry.query.order_by(Entry.created_at.desc()).all()
    ])


# =========================
# RUN
# =========================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
