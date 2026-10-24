import datetime
import functools
import json
import math
import os
import re
import secrets
import sqlite3
import uuid
from pathlib import Path

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from forecast_engine import ForecastInputError, load_monthly_series, monthly_forecast
from local_ai import LocalAIError, chat as local_ai_chat, configured_model, create_forecast as local_ai_forecast, status as local_ai_status

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('TVS_DB_PATH', str(BASE_DIR / 'tvs_inventory.db'))).expanduser().resolve()
MODEL_PATH = BASE_DIR / 'model_registry' / 'advanced_sales_pipeline.pkl'
METADATA_PATH = BASE_DIR / 'model_registry' / 'model_metadata.json'


def _persistent_session_secret():
    secret_path = BASE_DIR / '.session_secret'
    try:
        return secret_path.read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        secret = secrets.token_hex(32)
        try:
            with secret_path.open('x', encoding='utf-8') as handle:
                handle.write(secret)
            return secret
        except FileExistsError:
            return secret_path.read_text(encoding='utf-8').strip()

app = Flask(__name__, template_folder=str(BASE_DIR / 'templates'), static_folder=str(BASE_DIR / 'static'))
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024
app.config['SECRET_KEY'] = os.environ.get('TVS_SECRET_KEY') or _persistent_session_secret()
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(hours=12)


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status

def get_db_connection():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 10000')
    return conn

def init_db():
    conn = get_db_connection()
    # 1. Inventory Master Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit_price REAL NOT NULL,
            current_stock INTEGER NOT NULL DEFAULT 0,
            reorder_level INTEGER NOT NULL DEFAULT 10,
            created_at TEXT NOT NULL
        )
    ''')
    # 2. Sales Logs Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_id TEXT NOT NULL,
            sale_date TEXT NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            workshop_service_count INTEGER DEFAULT 30,
            quantity INTEGER NOT NULL
        )
    ''')
    # 3. Saved Invoices Master Record Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            customer_phone TEXT NOT NULL,
            vehicle_info TEXT,
            subtotal REAL NOT NULL,
            tax REAL NOT NULL,
            grand_total REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    # 4. Invoice Line Items Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            part_name TEXT NOT NULL,
            unit_price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            subtotal REAL NOT NULL,
            FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE CASCADE
        )
    ''')
    # 5. Workshop Repair Job Cards Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS job_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_number TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            vehicle_info TEXT NOT NULL,
            mechanic_name TEXT NOT NULL,
            description TEXT,
            part_id INTEGER,
            part_name TEXT,
            quantity INTEGER DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'In Progress',
            created_at TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()


def _columns(conn, table_name):
    return {row['name'] for row in conn.execute(f'PRAGMA table_info({table_name})')}


def upgrade_schema():
    """Apply additive, idempotent schema upgrades without reseeding user data."""
    conn = get_db_connection()
    try:
        with conn:
            conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
            conn.execute('''CREATE TABLE IF NOT EXISTS stock_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_id INTEGER NOT NULL,
                quantity_delta INTEGER NOT NULL,
                movement_type TEXT NOT NULL,
                reference TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (part_id) REFERENCES inventory(id)
            )''')
            conn.execute('''CREATE TABLE IF NOT EXISTS job_card_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_card_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_card_id) REFERENCES job_cards(id) ON DELETE CASCADE
            )''')
            if 'part_id' not in _columns(conn, 'invoice_items'):
                conn.execute('ALTER TABLE invoice_items ADD COLUMN part_id INTEGER')
            if 'updated_at' not in _columns(conn, 'job_cards'):
                conn.execute('ALTER TABLE job_cards ADD COLUMN updated_at TEXT')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_sales_part_date ON sales(part_id, sale_date)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sale_date)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_movements_part_date ON stock_movements(part_id, created_at)')
            conn.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, ?)", (datetime.datetime.now().isoformat(timespec='seconds'),))
    finally:
        conn.close()


upgrade_schema()


def _admin_user_count():
    conn = get_db_connection()
    try:
        return int(conn.execute('SELECT COUNT(*) FROM admin_users').fetchone()[0])
    finally:
        conn.close()


def _csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe(32)
    return session['_csrf_token']


@app.context_processor
def inject_template_globals():
    return {'csrf_token': _csrf_token, 'current_admin': session.get('admin_username')}


@app.before_request
def require_admin_login():
    if request.endpoint == 'static':
        return None
    if request.endpoint in {'setup', 'login'}:
        if _admin_user_count() == 0:
            return None
        if not session.get('admin_id'):
            return None
    elif not session.get('admin_id'):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Sign in as an administrator to continue.'}), 401
        return redirect(url_for('setup' if _admin_user_count() == 0 else 'login'))

    if session.get('admin_id'):
        expected = session.get('_csrf_token')
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and request.endpoint not in {'setup', 'login'}:
            supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
            if not expected or not secrets.compare_digest(str(supplied or ''), str(expected)):
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'Your session token expired. Refresh the page and try again.'}), 403
                abort(403)
    return None


def admin_required(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        if not session.get('admin_id'):
            return redirect(url_for('login'))
        return function(*args, **kwargs)
    return wrapped


def _apply_local_ai_forecast(result, horizon, baseline_estimate=None):
    if not result.get('forecast_available'):
        return result
    ai_result = local_ai_forecast(result['item'], result, horizon, baseline_estimate)
    result['forecast'] = ai_result['forecast']
    result['method'] = f"Local AI forecast · {ai_result['model']}"
    result['ai_model'] = ai_result['model']
    result['ai_summary'] = ai_result['summary']
    result['validation'] = None
    result['warnings'] = [warning for warning in result.get('warnings', [])
                          if 'rolling-origin accuracy check' not in warning]
    result['warnings'].append('AI-generated forecasts have not yet been backtested against later recorded sales. Review them before ordering.')
    if result['forecast'] and result.get('reorder'):
        first_forecast = result['forecast'][0]
        lead_demand = sum(entry['units'] for entry in result['forecast'][:min(3, horizon)])
        buffer = max(0, first_forecast['range_high'] - first_forecast['units'])
        target = max(int(result['item']['reorder_level']), lead_demand + buffer)
        result['reorder'] = {
            'current_stock': int(result['item']['current_stock']),
            'lead_demand_units': int(lead_demand),
            'target_stock': int(target),
            'suggested_order_units': max(0, int(target - int(result['item']['current_stock']))),
            'basis': 'AI forecast for up to three months plus the AI planning-range buffer. Review supplier lead time and confirm before ordering.',
        }
    return result


@app.errorhandler(APIError)
def handle_api_error(error):
    return jsonify({'error': error.message}), error.status


@app.errorhandler(413)
def handle_large_request(_error):
    return jsonify({'error': 'Request payload is too large.'}), 413


def _json_payload():
    if not request.is_json:
        raise APIError('Send a JSON request body with Content-Type: application/json.')
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise APIError('The request body must be a JSON object.')
    return data


def _text(data, key, label, required=True, maximum=160, default=''):
    value = data.get(key, default)
    if value is None:
        value = ''
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if required and not value:
        raise APIError(f'{label} is required.')
    if len(value) > maximum:
        raise APIError(f'{label} must be {maximum} characters or fewer.')
    return value


def _integer(value, label, minimum=None, maximum=None):
    if isinstance(value, bool):
        raise APIError(f'{label} must be a whole number.')
    try:
        number = int(value)
        if float(value) != number:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise APIError(f'{label} must be a whole number.')
    if minimum is not None and number < minimum:
        raise APIError(f'{label} must be at least {minimum}.')
    if maximum is not None and number > maximum:
        raise APIError(f'{label} must be no more than {maximum}.')
    return number


def _money(value, label, minimum=0.0, maximum=100000000.0):
    if isinstance(value, bool):
        raise APIError(f'{label} must be a valid amount.')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise APIError(f'{label} must be a valid amount.')
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise APIError(f'{label} must be between {minimum:g} and {maximum:g}.')
    return round(number, 2)


def _movement(conn, part_id, delta, movement_type, reference=None, notes=None):
    conn.execute('''INSERT INTO stock_movements
        (part_id, quantity_delta, movement_type, reference, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?)''', (
        part_id, delta, movement_type, reference, notes,
        datetime.datetime.now().isoformat(timespec='seconds'),
    ))

# ==============================================================================
# HTML VIEW ROUTES
# ==============================================================================

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if _admin_user_count() > 0:
        return redirect(url_for('login'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirmation = request.form.get('confirm_password', '')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,32}', username):
            error = 'Username must be 3–32 characters using letters, numbers, dot, dash, or underscore.'
        elif len(password) < 10:
            error = 'Choose a password with at least 10 characters.'
        elif password != confirmation:
            error = 'The passwords do not match.'
        else:
            conn = get_db_connection()
            try:
                with conn:
                    conn.execute('BEGIN IMMEDIATE')
                    count = conn.execute('SELECT COUNT(*) FROM admin_users').fetchone()[0]
                    if count:
                        error = 'Administrator setup has already been completed. Sign in instead.'
                    else:
                        conn.execute('INSERT INTO admin_users (username, password_hash, created_at) VALUES (?, ?, ?)',
                                     (username, generate_password_hash(password), datetime.datetime.now().isoformat(timespec='seconds')))
            except sqlite3.IntegrityError:
                error = 'That username is already in use.'
            finally:
                conn.close()
            if not error:
                return redirect(url_for('login', created='1'))
    return render_template('auth.html', mode='setup', error=error)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if _admin_user_count() == 0:
        return redirect(url_for('setup'))
    if session.get('admin_id'):
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_db_connection()
        try:
            user = conn.execute('SELECT id, username, password_hash FROM admin_users WHERE username = ? COLLATE NOCASE', (username,)).fetchone()
        finally:
            conn.close()
        if not user or not check_password_hash(user['password_hash'], password):
            error = 'Username or password is incorrect.'
        else:
            session.clear()
            session.permanent = True
            session['admin_id'] = int(user['id'])
            session['admin_username'] = user['username']
            _csrf_token()
            return redirect(url_for('index'))
    return render_template('auth.html', mode='login', error=error, created=request.args.get('created') == '1')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/admin/users', methods=['GET', 'POST'])
@admin_required
def admin_users():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirmation = request.form.get('confirm_password', '')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,32}', username):
            error = 'Username must be 3–32 characters using letters, numbers, dot, dash, or underscore.'
        elif len(password) < 10:
            error = 'Choose a password with at least 10 characters.'
        elif password != confirmation:
            error = 'The passwords do not match.'
        else:
            conn = get_db_connection()
            try:
                with conn:
                    conn.execute('INSERT INTO admin_users (username, password_hash, created_at) VALUES (?, ?, ?)',
                                 (username, generate_password_hash(password), datetime.datetime.now().isoformat(timespec='seconds')))
                flash(f'Administrator account “{username}” was added.', 'success')
                return redirect(url_for('admin_users'))
            except sqlite3.IntegrityError:
                error = 'That username is already in use.'
            finally:
                conn.close()

    conn = get_db_connection()
    try:
        users = [dict(row) for row in conn.execute('SELECT id, username, created_at FROM admin_users ORDER BY username COLLATE NOCASE')]
    finally:
        conn.close()
    return render_template('admin_users.html', users=users, error=error)

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/inventory')
def inventory():
    return render_template('inventory.html')

@app.route('/analytics')
def analytics():
    return render_template('analytics.html')

@app.route('/billing')
def billing():
    return render_template('billing.html')

@app.route('/service-hub')
def service_hub():
    return render_template('service_hub.html')

@app.route('/customer-ledger')
def customer_ledger():
    return render_template('customer_ledger.html')

@app.route('/forecast-hub')
def forecast_hub():
    return render_template('monthly_forecast.html')

# ==============================================================================
# REST API ENDPOINTS
# ==============================================================================

# --- INVENTORY CRUD APIs ---

@app.route('/api/inventory', methods=['GET'])
def get_inventory():
    conn = get_db_connection()
    try:
        query = request.args.get('q', '').strip()[:100]
        category = request.args.get('category', '').strip()[:80]
        low_stock = request.args.get('low_stock', '').lower() in ('1', 'true', 'yes')
        sql = 'SELECT * FROM inventory WHERE 1=1'
        params = []
        if query:
            sql += ' AND (part_name LIKE ? OR category LIKE ?)'
            params.extend((f'%{query}%', f'%{query}%'))
        if category:
            sql += ' AND lower(category) = lower(?)'
            params.append(category)
        if low_stock:
            sql += ' AND current_stock <= reorder_level'
        rows = conn.execute(sql + ' ORDER BY id DESC', params).fetchall()
        return jsonify([dict(row) for row in rows]), 200
    finally:
        conn.close()

@app.route('/api/inventory', methods=['POST'])
def add_product():
    data = _json_payload()
    part_name = _text(data, 'part_name', 'Product name', maximum=120)
    category = _text(data, 'category', 'Category', maximum=80, default='General') or 'General'
    unit_price = _money(data.get('unit_price', 0), 'Unit price')
    current_stock = _integer(data.get('current_stock', 0), 'Current stock', minimum=0, maximum=100000000)
    reorder_level = _integer(data.get('reorder_level', 10), 'Reorder level', minimum=0, maximum=100000000)
    today_str = datetime.date.today().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.execute('''INSERT INTO inventory
                (part_name, category, unit_price, current_stock, reorder_level, created_at)
                VALUES (?, ?, ?, ?, ?, ?)''',
                (part_name, category, unit_price, current_stock, reorder_level, today_str))
            new_id = cursor.lastrowid
            if current_stock:
                _movement(conn, new_id, current_stock, 'opening_balance', notes='Initial stock on product creation')
        return jsonify({'message': 'Product added successfully', 'product_id': new_id}), 201
    finally:
        conn.close()

@app.route('/api/inventory/<int:part_id>', methods=['PUT'])
def update_product(part_id):
    data = _json_payload()
    conn = get_db_connection()
    try:
        with conn:
            current = conn.execute('SELECT * FROM inventory WHERE id = ?', (part_id,)).fetchone()
            if not current:
                raise APIError('Product not found.', 404)
            part_name = _text(data, 'part_name', 'Product name', maximum=120, default=current['part_name'])
            category = _text(data, 'category', 'Category', maximum=80, default=current['category']) or 'General'
            unit_price = _money(data.get('unit_price', current['unit_price']), 'Unit price')
            current_stock = _integer(data.get('current_stock', current['current_stock']), 'Current stock', minimum=0, maximum=100000000)
            reorder_level = _integer(data.get('reorder_level', current['reorder_level']), 'Reorder level', minimum=0, maximum=100000000)
            delta = current_stock - int(current['current_stock'])
            conn.execute('''UPDATE inventory SET part_name = ?, category = ?, unit_price = ?, current_stock = ?, reorder_level = ? WHERE id = ?''',
                         (part_name, category, unit_price, current_stock, reorder_level, part_id))
            if delta:
                _movement(conn, part_id, delta, 'manual_adjustment', notes='Stock changed from inventory editor')
        return jsonify({'message': 'Product updated successfully'}), 200
    finally:
        conn.close()

@app.route('/api/inventory/<int:part_id>', methods=['DELETE'])
def delete_product(part_id):
    conn = get_db_connection()
    try:
        with conn:
            item = conn.execute('SELECT id FROM inventory WHERE id = ?', (part_id,)).fetchone()
            if not item:
                raise APIError('Product not found.', 404)
            has_sales = conn.execute('SELECT 1 FROM sales WHERE part_id = ? LIMIT 1', (str(part_id),)).fetchone()
            has_invoices = conn.execute('SELECT 1 FROM invoice_items WHERE part_id = ? LIMIT 1', (part_id,)).fetchone()
            has_jobs = conn.execute('SELECT 1 FROM job_cards WHERE part_id = ? LIMIT 1', (part_id,)).fetchone()
            has_movements = conn.execute('SELECT 1 FROM stock_movements WHERE part_id = ? LIMIT 1', (part_id,)).fetchone()
            if has_sales or has_invoices or has_jobs or has_movements:
                raise APIError('This product has sales, invoice, or workshop history and cannot be deleted. Keep it for accurate records.', 409)
            conn.execute('DELETE FROM inventory WHERE id = ?', (part_id,))
        return jsonify({'message': 'Product deleted successfully'}), 200
    finally:
        conn.close()

# --- SALES APIS ---

@app.route('/api/sales', methods=['POST'])
def record_sales():
    data = _json_payload()
    part_id = _integer(data.get('part_id'), 'Product', minimum=1)
    quantity = _integer(data.get('units_sold', data.get('quantity', 1)), 'Quantity sold', minimum=1, maximum=1000000)
    try:
        sale_date = datetime.date.fromisoformat(str(data.get('sale_date', datetime.date.today().isoformat())))
    except (TypeError, ValueError):
        raise APIError('Sale date must use YYYY-MM-DD format.')
    if sale_date > datetime.date.today():
        raise APIError('Sale date cannot be in the future.')
    conn = get_db_connection()
    try:
        with conn:
            item = conn.execute('SELECT unit_price FROM inventory WHERE id = ?', (part_id,)).fetchone()
            if not item:
                raise APIError('Product not found.', 404)
            changed = conn.execute('UPDATE inventory SET current_stock = current_stock - ? WHERE id = ? AND current_stock >= ?',
                                   (quantity, part_id, quantity)).rowcount
            if changed != 1:
                raise APIError('Not enough stock to record this sale.', 409)
            conn.execute('''INSERT INTO sales (part_id, sale_date, month, year, unit_price, workshop_service_count, quantity)
                VALUES (?, ?, ?, ?, ?, NULL, ?)''',
                (str(part_id), sale_date.isoformat(), sale_date.month, sale_date.year, item['unit_price'], quantity))
            _movement(conn, part_id, -quantity, 'sale', notes='Manual sale recorded')
        return jsonify({'message': 'Sales recorded and stock updated.'}), 201
    finally:
        conn.close()

# --- DYNAMIC ANALYTICS APIs (FOR LIVE REFRESH OF CHARTS) ---

@app.route('/api/analytics/sales-summary', methods=['GET'])
def get_sales_analytics():
    year = request.args.get('year', default=datetime.date.today().year, type=int)
    if year < 2000 or year > 2100:
        raise APIError('Year must be between 2000 and 2100.')
    part_id = request.args.get('part_id', default='ALL', type=str)
    conn = get_db_connection()
    try:
        monthly_sql = '''SELECT CAST(substr(s.sale_date, 6, 2) AS INTEGER) AS month,
            SUM(s.quantity) AS total_units, SUM(s.quantity * s.unit_price) AS total_revenue
            FROM sales s WHERE substr(s.sale_date, 1, 4) = ?'''
        params = [str(year)]
        if part_id != 'ALL':
            monthly_sql += ' AND s.part_id = ?'
            params.append(part_id)
        monthly_sql += ' GROUP BY substr(s.sale_date, 6, 2) ORDER BY month'
        monthly_rows = conn.execute(monthly_sql, params).fetchall()
        share_sql = '''SELECT s.part_id,
            COALESCE(i.part_name, 'Unmapped part ' || s.part_id) AS part_name,
            SUM(s.quantity) AS total_units, SUM(s.quantity * s.unit_price) AS total_revenue
            FROM sales s LEFT JOIN inventory i ON i.id = CAST(s.part_id AS INTEGER)
            WHERE substr(s.sale_date, 1, 4) = ?'''
        share_params = [str(year)]
        if part_id != 'ALL':
            share_sql += ' AND s.part_id = ?'
            share_params.append(part_id)
        share_sql += ' GROUP BY s.part_id ORDER BY total_units DESC'
        share_rows = conn.execute(share_sql, share_params).fetchall()
        month_lookup = {int(row['month']): dict(row) for row in monthly_rows}
        monthly_data = [month_lookup.get(month, {'month': month, 'total_units': 0, 'total_revenue': 0}) for month in range(1, 13)]
        return jsonify({'monthly_data': monthly_data, 'product_share': [dict(row) for row in share_rows]}), 200
    finally:
        conn.close()

# --- WHATSAPP INVOICING & SAVED RECORDS APIS ---

@app.route('/api/invoices', methods=['POST'])
def save_invoice():
    data = _json_payload()
    cust_name = _text(data, 'customer_name', 'Customer name', maximum=120)
    raw_phone = _text(data, 'customer_phone', 'Customer phone', maximum=24)
    cust_phone = re.sub(r'\D', '', raw_phone)
    if not 7 <= len(cust_phone) <= 15:
        raise APIError('Customer phone must contain 7 to 15 digits.')
    vehicle_info = _text(data, 'vehicle_info', 'Vehicle information', required=False, maximum=120)
    items = data.get('items')
    if not isinstance(items, list) or not items or len(items) > 100:
        raise APIError('Add between 1 and 100 invoice line items.')
    today_dt = datetime.datetime.now().replace(microsecond=0)
    today_str = today_dt.isoformat(sep=' ')
    inv_number = f"TVS-{today_dt.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    conn = get_db_connection()
    try:
        with conn:
            validated = []
            for line in items:
                if not isinstance(line, dict):
                    raise APIError('Each invoice line must be an object.')
                product_id = _integer(line.get('id'), 'Product ID', minimum=1)
                quantity = _integer(line.get('qty', line.get('quantity')), 'Line quantity', minimum=1, maximum=1000000)
                product = conn.execute('SELECT id, part_name, unit_price FROM inventory WHERE id = ?', (product_id,)).fetchone()
                if not product:
                    raise APIError(f'Product {product_id} was not found.', 404)
                validated.append({
                    'id': product_id,
                    'name': product['part_name'],
                    'price': float(product['unit_price']),
                    'qty': quantity,
                    'subtotal': round(float(product['unit_price']) * quantity, 2),
                })
            subtotal = round(sum(line['subtotal'] for line in validated), 2)
            tax = round(subtotal * 0.18, 2)
            grand_total = round(subtotal + tax, 2)
            cursor = conn.execute('''INSERT INTO invoices
                (invoice_number, customer_name, customer_phone, vehicle_info, subtotal, tax, grand_total, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (inv_number, cust_name, cust_phone, vehicle_info, subtotal, tax, grand_total, today_str))
            invoice_id = cursor.lastrowid
            for line in validated:
                changed = conn.execute('UPDATE inventory SET current_stock = current_stock - ? WHERE id = ? AND current_stock >= ?',
                                       (line['qty'], line['id'], line['qty'])).rowcount
                if changed != 1:
                    raise APIError(f"Not enough stock for {line['name']}.", 409)
                conn.execute('''INSERT INTO invoice_items
                    (invoice_id, part_id, part_name, unit_price, quantity, subtotal)
                    VALUES (?, ?, ?, ?, ?, ?)''',
                    (invoice_id, line['id'], line['name'], line['price'], line['qty'], line['subtotal']))
                conn.execute('''INSERT INTO sales
                    (part_id, sale_date, month, year, unit_price, workshop_service_count, quantity)
                    VALUES (?, ?, ?, ?, ?, NULL, ?)''',
                    (str(line['id']), today_dt.date().isoformat(), today_dt.month, today_dt.year, line['price'], line['qty']))
                _movement(conn, line['id'], -line['qty'], 'invoice_sale', inv_number)
        return jsonify({'message': 'Invoice saved successfully', 'invoice_id': invoice_id,
                        'invoice_number': inv_number, 'subtotal': subtotal, 'tax': tax,
                        'grand_total': grand_total, 'tax_rate': 0.18, 'items': validated}), 201
    finally:
        conn.close()

@app.route('/api/invoices', methods=['GET'])
def get_invoices():
    conn = get_db_connection()
    try:
        invoices = conn.execute('SELECT * FROM invoices ORDER BY id DESC LIMIT 500').fetchall()
        return jsonify([dict(inv) for inv in invoices]), 200
    finally:
        conn.close()


@app.route('/api/invoices/<int:invoice_id>', methods=['GET'])
def get_invoice(invoice_id):
    conn = get_db_connection()
    try:
        invoice = conn.execute('SELECT * FROM invoices WHERE id = ?', (invoice_id,)).fetchone()
        if not invoice:
            raise APIError('Invoice not found.', 404)
        items = conn.execute('SELECT part_id, part_name, unit_price, quantity, subtotal FROM invoice_items WHERE invoice_id = ?', (invoice_id,)).fetchall()
        return jsonify({'invoice': dict(invoice), 'items': [dict(row) for row in items]}), 200
    finally:
        conn.close()

# --- WORKSHOP SERVICE JOB CARDS APIS ---

@app.route('/api/jobcards', methods=['GET'])
def get_jobcards():
    conn = get_db_connection()
    try:
        jobs = conn.execute('SELECT * FROM job_cards ORDER BY id DESC LIMIT 500').fetchall()
        return jsonify([dict(job) for job in jobs]), 200
    finally:
        conn.close()

@app.route('/api/jobcards', methods=['POST'])
def create_jobcard():
    data = _json_payload()
    customer_name = _text(data, 'customer_name', 'Customer name', maximum=120)
    vehicle_info = _text(data, 'vehicle_info', 'Vehicle information', maximum=120)
    mechanic_name = _text(data, 'mechanic_name', 'Mechanic name', maximum=120)
    description = _text(data, 'description', 'Job description', required=False, maximum=1000, default='Service Repair') or 'Service Repair'
    raw_part_id = data.get('part_id')
    part_id = _integer(raw_part_id, 'Product ID', minimum=1) if raw_part_id not in (None, '') else None
    quantity = _integer(data.get('quantity', 1), 'Part quantity', minimum=1, maximum=1000000)
    today_dt = datetime.datetime.now().replace(microsecond=0)
    today_str = today_dt.isoformat(sep=' ')
    job_number = f"JOB-{today_dt.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:5].upper()}"
    conn = get_db_connection()
    try:
        with conn:
            part_name = None
            if part_id is not None:
                part = conn.execute('SELECT part_name FROM inventory WHERE id = ?', (part_id,)).fetchone()
                if not part:
                    raise APIError('Selected workshop part was not found.', 404)
                part_name = part['part_name']
            cursor = conn.execute('''INSERT INTO job_cards
                (job_number, customer_name, vehicle_info, mechanic_name, description, part_id, part_name, quantity, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'In Progress', ?, ?)''',
                (job_number, customer_name, vehicle_info, mechanic_name, description, part_id, part_name, quantity, today_str, today_str))
            conn.execute('INSERT INTO job_card_events(job_card_id, status, notes, created_at) VALUES (?, ?, ?, ?)',
                         (cursor.lastrowid, 'In Progress', 'Job card created', today_str))
        return jsonify({'message': 'Job card created.', 'job_number': job_number}), 201
    finally:
        conn.close()

@app.route('/api/jobcards/<int:job_id>/complete', methods=['PUT'])
def complete_jobcard(job_id):
    conn = get_db_connection()
    now = datetime.datetime.now().replace(microsecond=0)
    timestamp = now.isoformat(sep=' ')
    try:
        with conn:
            job = conn.execute('SELECT * FROM job_cards WHERE id = ?', (job_id,)).fetchone()
            if not job:
                raise APIError('Job card not found.', 404)
            if job['status'] == 'Completed':
                return jsonify({'message': 'Job card is already completed.'}), 200
            if job['status'] == 'Cancelled':
                raise APIError('A cancelled job card cannot be completed.', 409)
            if job['part_id'] and job['quantity']:
                part = conn.execute('SELECT unit_price FROM inventory WHERE id = ?', (job['part_id'],)).fetchone()
                if not part:
                    raise APIError('The part on this job card no longer exists.', 409)
                changed = conn.execute('UPDATE inventory SET current_stock = current_stock - ? WHERE id = ? AND current_stock >= ?',
                                       (job['quantity'], job['part_id'], job['quantity'])).rowcount
                if changed != 1:
                    raise APIError('Not enough stock to complete this job card. Receive or adjust stock first.', 409)
                conn.execute('''INSERT INTO sales
                    (part_id, sale_date, month, year, unit_price, workshop_service_count, quantity)
                    VALUES (?, ?, ?, ?, ?, NULL, ?)''',
                    (str(job['part_id']), now.date().isoformat(), now.month, now.year, part['unit_price'], job['quantity']))
                _movement(conn, job['part_id'], -int(job['quantity']), 'workshop_issue', job['job_number'])
            conn.execute("UPDATE job_cards SET status = 'Completed', updated_at = ? WHERE id = ?", (timestamp, job_id))
            conn.execute('INSERT INTO job_card_events(job_card_id, status, notes, created_at) VALUES (?, ?, ?, ?)',
                         (job_id, 'Completed', 'Job marked complete', timestamp))
        return jsonify({'message': 'Job card completed and stock deducted.'}), 200
    finally:
        conn.close()


@app.route('/api/jobcards/<int:job_id>/status', methods=['PUT'])
def update_jobcard_status(job_id):
    data = _json_payload()
    status = _text(data, 'status', 'Status', maximum=40)
    allowed = {'In Progress', 'Waiting for Parts', 'Cancelled'}
    if status not in allowed:
        raise APIError('Status must be In Progress, Waiting for Parts, or Cancelled. Use the complete action to finish a job.')
    notes = _text(data, 'notes', 'Notes', required=False, maximum=500)
    timestamp = datetime.datetime.now().replace(microsecond=0).isoformat(sep=' ')
    conn = get_db_connection()
    try:
        with conn:
            job = conn.execute('SELECT status FROM job_cards WHERE id = ?', (job_id,)).fetchone()
            if not job:
                raise APIError('Job card not found.', 404)
            if job['status'] in ('Completed', 'Cancelled'):
                raise APIError(f"A {job['status'].lower()} job card cannot be changed.", 409)
            conn.execute('UPDATE job_cards SET status = ?, updated_at = ? WHERE id = ?', (status, timestamp, job_id))
            conn.execute('INSERT INTO job_card_events(job_card_id, status, notes, created_at) VALUES (?, ?, ?, ?)',
                         (job_id, status, notes or None, timestamp))
        return jsonify({'message': 'Job card status updated.', 'status': status}), 200
    finally:
        conn.close()

# --- VEHICLE HISTORY LEDGER API ---

@app.route('/api/customer-ledger', methods=['GET'])
def get_customer_ledger():
    conn = get_db_connection()
    try:
        invoices = conn.execute("SELECT invoice_number AS reference_no, created_at, customer_name, customer_phone, vehicle_info, grand_total AS amount, 'Invoice' AS source, NULL AS description, NULL AS status FROM invoices ORDER BY id DESC LIMIT 500").fetchall()
        jobs = conn.execute("SELECT job_number AS reference_no, created_at, customer_name, 'N/A' AS customer_phone, vehicle_info, part_name AS description, NULL AS amount, status, 'Job Card' AS source FROM job_cards ORDER BY id DESC LIMIT 500").fetchall()
        records = [dict(row) for row in invoices] + [dict(row) for row in jobs]
        records.sort(key=lambda row: row.get('created_at') or '', reverse=True)
        return jsonify(records), 200
    finally:
        conn.close()

@app.route('/api/forecasts/monthly', methods=['POST'])
def create_monthly_forecast():
    data = _json_payload()
    part_id = _integer(data.get('part_id'), 'Product ID', minimum=1)
    horizon = _integer(data.get('horizon', 6), 'Forecast horizon', minimum=1, maximum=12)
    raw_analog = data.get('analog_part_id')
    analog_part_id = _integer(raw_analog, 'Comparable product ID', minimum=1) if raw_analog not in (None, '') else None
    raw_baseline = data.get('baseline_estimate')
    baseline = None if raw_baseline in (None, '') else raw_baseline
    conn = get_db_connection()
    try:
        result = monthly_forecast(conn, part_id, horizon, analog_part_id, baseline)
        try:
            result = _apply_local_ai_forecast(result, horizon,
                                              float(baseline) if baseline not in (None, '') else None)
        except LocalAIError as error:
            raise APIError(str(error), 503)
        result['generated_at'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        return jsonify(result), 200
    except ForecastInputError as error:
        raise APIError(str(error), 422)
    finally:
        conn.close()


@app.route('/api/forecasts/monthly/<int:part_id>', methods=['GET'])
def get_monthly_forecast(part_id):
    horizon = request.args.get('horizon', default=6, type=int)
    analog = request.args.get('analog_part_id', default=None, type=int)
    baseline = request.args.get('baseline_estimate', default=None, type=float)
    conn = get_db_connection()
    try:
        result = monthly_forecast(conn, part_id, horizon, analog, baseline)
        try:
            result = _apply_local_ai_forecast(result, horizon, baseline)
        except LocalAIError as error:
            raise APIError(str(error), 503)
        result['generated_at'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        return jsonify(result), 200
    except ForecastInputError as error:
        raise APIError(str(error), 422)
    finally:
        conn.close()


@app.route('/api/predict/<int:part_id>', methods=['GET'])
def predict_sales(part_id):
    """Compatibility route; predictions are now monthly and based on linked sales."""
    horizon = request.args.get('horizon', default=6, type=int)
    conn = get_db_connection()
    try:
        result = monthly_forecast(conn, part_id, horizon)
        try:
            result = _apply_local_ai_forecast(result, horizon)
        except LocalAIError as error:
            raise APIError(str(error), 503)
        result['generated_at'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        return jsonify(result), 200
    except ForecastInputError as error:
        raise APIError(str(error), 422)
    finally:
        conn.close()


@app.route('/api/forecast/status', methods=['GET'])
def forecast_status():
    try:
        with METADATA_PATH.open('r', encoding='utf-8') as handle:
            metadata = json.load(handle)
    except (OSError, json.JSONDecodeError):
        metadata = None
    conn = get_db_connection()
    try:
        inventory_count = conn.execute('SELECT COUNT(*) FROM inventory').fetchone()[0]
        sales_count = conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0]
        mapped_count = conn.execute('''SELECT COUNT(*) FROM sales s JOIN inventory i
            ON i.id = CAST(s.part_id AS INTEGER) WHERE CAST(s.part_id AS INTEGER) > 0''').fetchone()[0]
        return jsonify({
            'monthly_engine': f"Local Ollama AI · {configured_model()}",
            'ai': local_ai_status(),
            'monthly_forecasts_use_legacy_pickle': False,
            'database': {
                'inventory_items': inventory_count,
                'sales_rows': sales_count,
                'sales_rows_linked_to_inventory': mapped_count,
                'sales_rows_without_inventory_match': max(0, sales_count - mapped_count),
            },
            'legacy_artifact': {
                'available': MODEL_PATH.is_file(),
                'metadata_available': metadata is not None,
                'metadata': metadata,
                'status': 'Archived daily model; not used by monthly forecasts. Stored metrics have not been revalidated against the current SQLite data.',
            },
        }), 200
    finally:
        conn.close()


@app.route('/api/ai/copilot', methods=['POST'])
def inventory_copilot():
    data = _json_payload()
    question = _text(data, 'question', 'Question', maximum=500)
    conn = get_db_connection()
    try:
        rows = conn.execute('''
            SELECT i.id, i.part_name, i.category, i.current_stock, i.reorder_level, i.unit_price,
                   COALESCE(SUM(CASE WHEN s.sale_date >= date('now', '-90 days') THEN s.quantity ELSE 0 END), 0) AS units_sold_90d,
                   MAX(s.sale_date) AS last_sale_date
            FROM inventory i
            LEFT JOIN sales s ON CAST(s.part_id AS INTEGER) = i.id AND CAST(s.part_id AS INTEGER) > 0
            GROUP BY i.id
            ORDER BY (i.current_stock - i.reorder_level), i.part_name COLLATE NOCASE
            LIMIT 200
        ''').fetchall()
    finally:
        conn.close()

    context = {
        'as_of_date': datetime.date.today().isoformat(),
        'products': [dict(row) for row in rows],
        'inventory_item_count': len(rows),
    }
    system = (
        'You are an inventory operations copilot. Answer briefly using only the supplied inventory and recorded sales data. '
        'Do not invent sales, suppliers, lead times, or stock changes. Identify missing information when needed. '
        'You may recommend actions but never claim to have changed the database or placed an order.'
    )
    try:
        answer = local_ai_chat(system, f"Inventory data: {json.dumps(context, ensure_ascii=False)}\n\nQuestion: {question}")
    except LocalAIError as error:
        raise APIError(str(error), 503)
    return jsonify({'answer': answer, 'model': configured_model()}), 200


@app.route('/api/forecast/products', methods=['GET'])
def forecast_products():
    conn = get_db_connection()
    try:
        products = conn.execute('SELECT id, part_name, category, unit_price, current_stock, reorder_level FROM inventory ORDER BY part_name COLLATE NOCASE').fetchall()
        response = []
        for product in products:
            series = load_monthly_series(conn, product['id'])
            response.append({**dict(product), 'history_months': len(series['values']),
                             'last_sale_date': series['last_sale_date'],
                             'forecast_ready': len(series['values']) >= 6})
        return jsonify(response), 200
    finally:
        conn.close()


@app.route('/api/inventory/<int:part_id>/movements', methods=['GET'])
def get_stock_movements(part_id):
    limit = request.args.get('limit', default=100, type=int)
    if not 1 <= limit <= 500:
        raise APIError('limit must be between 1 and 500.')
    conn = get_db_connection()
    try:
        rows = conn.execute('SELECT * FROM stock_movements WHERE part_id = ? ORDER BY id DESC LIMIT ?', (part_id, limit)).fetchall()
        return jsonify([dict(row) for row in rows]), 200
    finally:
        conn.close()


@app.route('/api/inventory/<int:part_id>/receive', methods=['POST'])
def receive_stock(part_id):
    data = _json_payload()
    quantity = _integer(data.get('quantity'), 'Received quantity', minimum=1, maximum=1_000_000)
    reference = _text(data, 'reference', 'Reference', required=False, maximum=80)
    notes = _text(data, 'notes', 'Notes', required=False, maximum=400)
    conn = get_db_connection()
    try:
        with conn:
            item = conn.execute('SELECT id FROM inventory WHERE id = ?', (part_id,)).fetchone()
            if not item:
                raise APIError('Product not found.', 404)
            conn.execute('UPDATE inventory SET current_stock = current_stock + ? WHERE id = ?', (quantity, part_id))
            _movement(conn, part_id, quantity, 'stock_received', reference or None, notes or None)
        updated = conn.execute('SELECT current_stock FROM inventory WHERE id = ?', (part_id,)).fetchone()
        return jsonify({'part_id': part_id, 'received_quantity': quantity, 'current_stock': updated['current_stock']}), 201
    finally:
        conn.close()


@app.route('/api/dashboard/summary', methods=['GET'])
def dashboard_summary():
    conn = get_db_connection()
    try:
        inventory_count = conn.execute('SELECT COUNT(*) FROM inventory').fetchone()[0]
        low_stock = conn.execute('SELECT COUNT(*) FROM inventory WHERE current_stock <= reorder_level').fetchone()[0]
        sales = conn.execute('SELECT COALESCE(SUM(quantity), 0), COUNT(*) FROM sales').fetchone()
        revenue = conn.execute("SELECT COALESCE(SUM(grand_total), 0) FROM invoices WHERE substr(created_at, 1, 7) = ?", (datetime.date.today().strftime('%Y-%m'),)).fetchone()[0]
        return jsonify({'inventory_items': inventory_count, 'low_stock_items': low_stock,
                        'recorded_sales_units': sales[0], 'sales_rows': sales[1],
                        'current_month_invoice_revenue': round(float(revenue), 2)}), 200
    finally:
        conn.close()


@app.route('/api/dashboard/overview', methods=['GET'])
def dashboard_overview():
    days = request.args.get('days', default=30, type=int)
    if days not in (7, 30, 90, 365):
        raise APIError('days must be 7, 30, 90, or 365.')
    today = datetime.date.today()
    current_start = today - datetime.timedelta(days=days - 1)
    previous_start = current_start - datetime.timedelta(days=days)
    previous_end = current_start - datetime.timedelta(days=1)
    category = request.args.get('category', '').strip()
    conn = get_db_connection()
    try:
        categories = [row['category'] for row in conn.execute('''
            SELECT MIN(category) AS category FROM inventory
            WHERE trim(category) <> '' GROUP BY lower(category) ORDER BY lower(category)
        ''')]
        if category and category.casefold() not in {value.casefold() for value in categories}:
            raise APIError('The selected inventory category does not exist.')

        inventory_sql = 'SELECT * FROM inventory'
        inventory_params = []
        if category:
            inventory_sql += ' WHERE lower(category) = lower(?)'
            inventory_params.append(category)
        inventory = [dict(row) for row in conn.execute(inventory_sql + ' ORDER BY part_name COLLATE NOCASE', inventory_params)]
        inventory_count = len(inventory)
        low_stock_items = [item for item in inventory if int(item['current_stock']) <= int(item['reorder_level'])]
        out_of_stock = [item for item in inventory if int(item['current_stock']) <= 0]
        negative_stock = [item for item in inventory if int(item['current_stock']) < 0]
        stock_value = sum(max(0, int(item['current_stock'])) * float(item['unit_price']) for item in inventory)

        rows = conn.execute('''SELECT s.sale_date, s.part_id, s.quantity, s.unit_price,
            i.id AS inventory_id, i.part_name, i.category
            FROM sales s LEFT JOIN inventory i ON i.id = CAST(s.part_id AS INTEGER)
            WHERE substr(s.sale_date, 1, 10) >= ? AND substr(s.sale_date, 1, 10) <= ?
            ORDER BY s.sale_date''', (previous_start.isoformat(), today.isoformat())).fetchall()

        grain = 'day' if days <= 30 else ('week' if days <= 90 else 'month')
        if grain == 'day':
            bucket_count = days
            labels = [(current_start + datetime.timedelta(days=index)).strftime('%d %b') for index in range(bucket_count)]
        elif grain == 'week':
            bucket_count = (days + 6) // 7
            labels = [(current_start + datetime.timedelta(days=7 * index)).strftime('%d %b') for index in range(bucket_count)]
        else:
            last_month = (today.year * 12 + today.month - 1) - (current_start.year * 12 + current_start.month - 1)
            bucket_count = last_month + 1
            labels = []
            for index in range(bucket_count):
                month_index = current_start.year * 12 + current_start.month - 1 + index
                year, month_zero = divmod(month_index, 12)
                labels.append(datetime.date(year, month_zero + 1, 1).strftime('%b %y'))
        current_series = [0] * bucket_count
        previous_series = [0] * bucket_count
        current_revenue_series = [0.0] * bucket_count
        previous_revenue_series = [0.0] * bucket_count
        current_units = previous_units = 0
        current_revenue = previous_revenue = 0.0
        current_rows = previous_rows = linked_current_rows = 0
        invalid_rows = 0
        item_sales = {}
        for row in rows:
            try:
                sale_date = datetime.date.fromisoformat(str(row['sale_date'])[:10])
                quantity = int(row['quantity'])
                price = float(row['unit_price'])
                if quantity < 0 or not math.isfinite(price) or price < 0:
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                invalid_rows += 1
                continue
            if category and (row['category'] is None or row['category'].casefold() != category.casefold()):
                continue
            revenue = quantity * price
            is_current = current_start <= sale_date <= today
            is_previous = previous_start <= sale_date <= previous_end
            if not is_current and not is_previous:
                continue
            anchor = current_start if is_current else previous_start
            day_offset = (sale_date - anchor).days
            bucket = day_offset if grain == 'day' else (day_offset // 7 if grain == 'week' else ((sale_date.year * 12 + sale_date.month - 1) - (anchor.year * 12 + anchor.month - 1)))
            if not 0 <= bucket < bucket_count:
                continue
            if is_current:
                current_units += quantity
                current_revenue += revenue
                current_rows += 1
                current_series[bucket] += quantity
                current_revenue_series[bucket] += revenue
                if row['inventory_id'] is not None:
                    linked_current_rows += 1
                    item = item_sales.setdefault(row['inventory_id'], {
                        'part_id': row['inventory_id'], 'part_name': row['part_name'],
                        'category': row['category'], 'units': 0, 'revenue': 0.0,
                    })
                    item['units'] += quantity
                    item['revenue'] += revenue
            else:
                previous_units += quantity
                previous_revenue += revenue
                previous_rows += 1
                previous_series[bucket] += quantity
                previous_revenue_series[bucket] += revenue

        if grain == 'day':
            labels = [(current_start + datetime.timedelta(days=index)).strftime('%d %b') for index in range(bucket_count)]
        elif grain == 'week':
            labels = [(current_start + datetime.timedelta(days=7 * index)).strftime('%d %b') for index in range(bucket_count)]
        top_products = sorted(item_sales.values(), key=lambda item: (-item['units'], item['part_name'].casefold()))[:5]
        for item in top_products:
            item['revenue'] = round(item['revenue'], 2)

        all_sales_rows = conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0]
        unmapped_rows = conn.execute('''SELECT COUNT(*) FROM sales s LEFT JOIN inventory i
            ON i.id = CAST(s.part_id AS INTEGER) WHERE i.id IS NULL''').fetchone()[0]
        total_inventory_rows = conn.execute('SELECT COUNT(*) FROM inventory').fetchone()[0]
        open_jobs = conn.execute("SELECT COUNT(*) FROM job_cards WHERE lower(status) NOT IN ('completed', 'cancelled')").fetchone()[0]
        change_pct = None if previous_units == 0 else round((current_units - previous_units) * 100.0 / previous_units, 1)
        restock_list = sorted(low_stock_items, key=lambda item: (int(item['current_stock']) > 0, int(item['current_stock']) - int(item['reorder_level']), item['part_name'].casefold()))[:12]
        for item in restock_list:
            item['stock_gap'] = max(0, int(item['reorder_level']) - int(item['current_stock']))
            item['risk'] = 'Negative balance' if int(item['current_stock']) < 0 else ('Out of stock' if int(item['current_stock']) == 0 else 'At or below reorder level')

        insights = []
        if negative_stock:
            first = negative_stock[0]
            insights.append({'level': 'danger', 'title': 'Reconcile a negative stock balance', 'detail': f"{first['part_name']} is recorded at {first['current_stock']} units. Verify stock before placing an order."})
        elif low_stock_items:
            insights.append({'level': 'warning', 'title': 'Review low-stock items', 'detail': f"{len(low_stock_items)} item(s) are at or below their reorder level."})
        else:
            insights.append({'level': 'success', 'title': 'Stock is above reorder levels', 'detail': 'No inventory items are currently at or below their reorder threshold.'})
        if top_products:
            leader = top_products[0]
            insights.append({'level': 'info', 'title': 'Top recorded seller in this range', 'detail': f"{leader['part_name']} has {leader['units']} linked unit(s) recorded."})
        elif current_rows == 0:
            insights.append({'level': 'secondary', 'title': 'No sales rows in this range', 'detail': 'The chart reflects recorded transactions; it does not fill missing periods with estimated sales.'})
        if unmapped_rows:
            insights.append({'level': 'secondary', 'title': 'Some sales are not linked to inventory', 'detail': f"{unmapped_rows} of {all_sales_rows} saved sales rows have product IDs that do not match inventory. They are excluded from product rankings."})

        return jsonify({
            'as_of': today.isoformat(), 'days': days, 'category': category or 'ALL',
            'inventory': {
                'items': inventory_count, 'stock_value_at_list_price': round(stock_value, 2),
                'low_stock_items': len(low_stock_items), 'out_of_stock_items': len(out_of_stock),
                'negative_stock_items': len(negative_stock),
            },
            'sales': {
                'current_units': current_units, 'previous_units': previous_units,
                'current_revenue': round(current_revenue, 2), 'previous_revenue': round(previous_revenue, 2),
                'current_rows': current_rows, 'previous_rows': previous_rows,
                'linked_current_rows': linked_current_rows, 'change_percent': change_pct,
                'series': {'grain': grain, 'labels': labels, 'current': current_series, 'previous': previous_series,
                           'current_revenue': [round(value, 2) for value in current_revenue_series],
                           'previous_revenue': [round(value, 2) for value in previous_revenue_series]},
            },
            'workshop': {'open_jobs': open_jobs},
            'categories': categories,
            'top_products': top_products,
            'restock_priorities': restock_list,
            'insights': insights,
            'data_quality': {'sales_rows_total': all_sales_rows, 'unmapped_sales_rows': unmapped_rows, 'invalid_rows_in_range': invalid_rows, 'negative_stock_items': len(negative_stock)},
        }), 200
    finally:
        conn.close()


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'API route or record not found.'}), 404
    return 'Page not found.', 404


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if isinstance(error, HTTPException):
        if request.path.startswith('/api/'):
            return jsonify({'error': error.description}), error.code
        return error
    app.logger.exception('Unhandled application error')
    if request.path.startswith('/api/'):
        return jsonify({'error': 'The request could not be completed. Check the application log for details.'}), 500
    return 'The application could not complete this request.', 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5000'))
    debug = os.environ.get('TVS_DEBUG', '').lower() in ('1', 'true', 'yes')
    app.run(host='127.0.0.1', port=port, debug=debug)
