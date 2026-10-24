import os
import sqlite3
import datetime
import joblib
import numpy as np
import pandas as pd
from flask import Flask, render_template, jsonify, request

from tvs_sales_pipeline import IntermittentDemandEstimator, TimeSeriesFeatureEngineer, HybridSKUEnsemblePredictor

app = Flask(__name__)

DB_PATH = 'tvs_inventory.db'
MODEL_PATH = os.path.join('model_registry', 'advanced_sales_pipeline.pkl')
METADATA_PATH = os.path.join('model_registry', 'model_metadata.json')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    conn.commit()
    conn.close()

init_db()

# ==============================================================================
# HTML VIEW ROUTES
# ==============================================================================

@app.route('/')
def index():
    return render_template('index.html')

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
    return render_template('forecast_hub.html')

# ==============================================================================
# REST API ENDPOINTS
# ==============================================================================

# --- INVENTORY CRUD APIs ---

@app.route('/api/inventory', methods=['GET'])
def get_inventory():
    conn = get_db_connection()
    items = conn.execute("SELECT * FROM inventory ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([dict(item) for item in items]), 200

@app.route('/api/inventory', methods=['POST'])
def add_product():
    data = request.get_json() or {}
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO inventory (part_name, category, unit_price, current_stock, reorder_level, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        data.get('part_name'),
        data.get('category', 'General'),
        float(data.get('unit_price', 0.0)),
        int(data.get('current_stock', 0)),
        int(data.get('reorder_level', 10)),
        today_str
    ))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return jsonify({'message': 'Product added successfully', 'product_id': new_id}), 201

@app.route('/api/inventory/<int:part_id>', methods=['PUT'])
def update_product(part_id):
    data = request.get_json() or {}
    conn = get_db_connection()
    conn.execute('''
        UPDATE inventory 
        SET part_name = ?, category = ?, unit_price = ?, current_stock = ?, reorder_level = ?
        WHERE id = ?
    ''', (
        data.get('part_name'),
        data.get('category'),
        float(data.get('unit_price', 0.0)),
        int(data.get('current_stock', 0)),
        int(data.get('reorder_level', 10)),
        part_id
    ))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Product updated successfully'}), 200

@app.route('/api/inventory/<int:part_id>', methods=['DELETE'])
def delete_product(part_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM sales WHERE part_id = ?", (str(part_id),))
    conn.execute("DELETE FROM inventory WHERE id = ?", (part_id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Product deleted successfully'}), 200

# --- SALES APIS ---

@app.route('/api/sales', methods=['POST'])
def record_sales():
    data = request.get_json() or {}
    part_id = str(data.get('part_id'))
    quantity = int(data.get('units_sold', 1))
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    month = int(data.get('month', datetime.date.today().month))
    year = int(data.get('year', datetime.date.today().year))

    conn = get_db_connection()
    cursor = conn.cursor()

    item = cursor.execute("SELECT unit_price, current_stock FROM inventory WHERE id = ?", (part_id,)).fetchone()
    unit_price = float(item['unit_price']) if item else 500.0

    cursor.execute('''
        INSERT INTO sales (part_id, sale_date, month, year, unit_price, workshop_service_count, quantity)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (part_id, today_str, month, year, unit_price, 35, quantity))

    if item and item['current_stock'] >= quantity:
        cursor.execute("UPDATE inventory SET current_stock = current_stock - ? WHERE id = ?", (quantity, part_id))

    conn.commit()
    conn.close()
    return jsonify({'message': 'Sales recorded successfully'}), 201

# --- DYNAMIC ANALYTICS APIs (FOR LIVE REFRESH OF CHARTS) ---

@app.route('/api/analytics/sales-summary', methods=['GET'])
def get_sales_analytics():
    """
    Returns dynamically calculated monthly sales totals and product shares.
    Reflects edits/additions from the inventory table immediately.
    """
    year = request.args.get('year', default=datetime.date.today().year, type=int)
    part_id = request.args.get('part_id', default='ALL', type=str)

    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Fetch Month-wiseAggregates
    if part_id != 'ALL':
        query = '''
            SELECT s.month, 
                   SUM(s.quantity) as total_units, 
                   SUM(s.quantity * s.unit_price) as total_revenue
            FROM sales s
            WHERE s.year = ? AND s.part_id = ?
            GROUP BY s.month
            ORDER BY s.month ASC
        '''
        monthly_rows = cursor.execute(query, (year, part_id)).fetchall()
    else:
        query = '''
            SELECT s.month, 
                   SUM(s.quantity) as total_units, 
                   SUM(s.quantity * s.unit_price) as total_revenue
            FROM sales s
            WHERE s.year = ?
            GROUP BY s.month
            ORDER BY s.month ASC
        '''
        monthly_rows = cursor.execute(query, (year,)).fetchall()

    # 2. Fetch Product Share Breakdown (Joining with Inventory to get live names)
    share_query = '''
        SELECT s.part_id,
               COALESCE(i.part_name, 'Part #' || s.part_id) as part_name,
               SUM(s.quantity) as total_units,
               SUM(s.quantity * s.unit_price) as total_revenue
        FROM sales s
        LEFT JOIN inventory i ON i.id = CAST(s.part_id AS INTEGER)
        WHERE s.year = ?
        GROUP BY s.part_id
    '''
    share_rows = cursor.execute(share_query, (year,)).fetchall()

    conn.close()

    return jsonify({
        'monthly_data': [dict(r) for r in monthly_rows],
        'product_share': [dict(s) for s in share_rows]
    }), 200

# --- WHATSAPP INVOICING & SAVED RECORDS APIS ---

@app.route('/api/invoices', methods=['POST'])
def save_invoice():
    data = request.get_json() or {}
    cust_name = data.get('customer_name')
    cust_phone = data.get('customer_phone')
    vehicle_info = data.get('vehicle_info', '')
    items = data.get('items', [])
    subtotal = float(data.get('subtotal', 0.0))
    tax = float(data.get('tax', 0.0))
    grand_total = float(data.get('grand_total', 0.0))
    
    today_dt = datetime.datetime.now()
    today_str = today_dt.strftime('%Y-%m-%d %H:%M')
    inv_number = f"TVS-{today_dt.strftime('%Y%m%d%H%M%S')}"

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO invoices (invoice_number, customer_name, customer_phone, vehicle_info, subtotal, tax, grand_total, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (inv_number, cust_name, cust_phone, vehicle_info, subtotal, tax, grand_total, today_str))
    
    invoice_id = cursor.lastrowid

    for item in items:
        item_subtotal = float(item['price']) * int(item['qty'])
        cursor.execute('''
            INSERT INTO invoice_items (invoice_id, part_name, unit_price, quantity, subtotal)
            VALUES (?, ?, ?, ?, ?)
        ''', (invoice_id, item['name'], float(item['price']), int(item['qty']), item_subtotal))

        cursor.execute("UPDATE inventory SET current_stock = MAX(0, current_stock - ?) WHERE id = ?", (int(item['qty']), item['id']))
        
        cursor.execute('''
            INSERT INTO sales (part_id, sale_date, month, year, unit_price, workshop_service_count, quantity)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (str(item['id']), today_dt.strftime('%Y-%m-%d'), today_dt.month, today_dt.year, float(item['price']), 35, int(item['qty'])))

    conn.commit()
    conn.close()
    return jsonify({'message': 'Invoice saved successfully', 'invoice_number': inv_number}), 201

@app.route('/api/invoices', methods=['GET'])
def get_invoices():
    conn = get_db_connection()
    invoices = conn.execute("SELECT * FROM invoices ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([dict(inv) for inv in invoices]), 200

# --- WORKSHOP SERVICE JOB CARDS APIS ---

@app.route('/api/jobcards', methods=['GET'])
def get_jobcards():
    conn = get_db_connection()
    jobs = conn.execute("SELECT * FROM job_cards ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([dict(j) for j in jobs]), 200

@app.route('/api/jobcards', methods=['POST'])
def create_jobcard():
    data = request.get_json() or {}
    today_dt = datetime.datetime.now()
    today_str = today_dt.strftime('%Y-%m-%d %H:%M')
    job_number = f"JOB-{today_dt.strftime('%m%d%H%M%S')}"

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO job_cards (job_number, customer_name, vehicle_info, mechanic_name, description, part_id, part_name, quantity, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        job_number,
        data.get('customer_name'),
        data.get('vehicle_info'),
        data.get('mechanic_name'),
        data.get('description', 'Service Repair'),
        data.get('part_id'),
        data.get('part_name'),
        int(data.get('quantity', 1)),
        'In Progress',
        today_str
    ))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Job Card created', 'job_number': job_number}), 201

@app.route('/api/jobcards/<int:job_id>/complete', methods=['PUT'])
def complete_jobcard(job_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    job = cursor.execute("SELECT * FROM job_cards WHERE id = ?", (job_id,)).fetchone()
    if job and job['status'] != 'Completed':
        cursor.execute("UPDATE job_cards SET status = 'Completed' WHERE id = ?", (job_id,))
        
        if job['part_id'] and job['quantity']:
            cursor.execute("UPDATE inventory SET current_stock = MAX(0, current_stock - ?) WHERE id = ?", (job['quantity'], job['part_id']))
            
            today = datetime.date.today()
            cursor.execute('''
                INSERT INTO sales (part_id, sale_date, month, year, unit_price, workshop_service_count, quantity)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (str(job['part_id']), today.strftime('%Y-%m-%d'), today.month, today.year, 450.0, 35, job['quantity']))

    conn.commit()
    conn.close()
    return jsonify({'message': 'Job Card completed and stock deducted'}), 200

# --- VEHICLE HISTORY LEDGER API ---

@app.route('/api/customer-ledger', methods=['GET'])
def get_customer_ledger():
    conn = get_db_connection()
    invoices = conn.execute("SELECT invoice_number as reference_no, created_at, customer_name, customer_phone, vehicle_info, grand_total as amount, 'Invoice' as source FROM invoices ORDER BY id DESC").fetchall()
    jobs = conn.execute("SELECT job_number as reference_no, created_at, customer_name, 'N/A' as customer_phone, vehicle_info, part_name as description, status, 'Job Card' as source FROM job_cards ORDER BY id DESC").fetchall()
    conn.close()

    records = [dict(i) for i in invoices] + [dict(j) for j in jobs]
    return jsonify(records), 200

# --- UPGRADED ML PREDICTION API (MATCHING TVS_SALES_PIPELINE.PY V2.4.0) ---

@app.route('/api/predict/<part_id>', methods=['GET'])
def predict_sales(part_id):
    if not os.path.exists(MODEL_PATH):
        return jsonify({'error': 'Model binary not found. Run tvs_sales_pipeline.py first.'}), 500

    pipeline = joblib.load(MODEL_PATH)
    today = datetime.date.today()
    
    # Calculate cyclical sine & cosine embeddings
    day_of_week = today.weekday()
    month = today.month
    sin_day_of_week = np.sin(2 * np.pi * day_of_week / 7)
    cos_day_of_week = np.cos(2 * np.pi * day_of_week / 7)
    sin_month = np.sin(2 * np.pi * month / 12)
    cos_month = np.cos(2 * np.pi * month / 12)

    # Fetch unit price dynamically from database if possible
    conn = get_db_connection()
    item = conn.execute("SELECT unit_price FROM inventory WHERE id = ?", (part_id,)).fetchone()
    conn.close()
    
    unit_price = float(item['unit_price']) if item else (450.0 if 'BRAKE' in str(part_id).upper() else 180.0)

    # Input dataframe matching all features from upgraded tvs_sales_pipeline.py
    input_data = pd.DataFrame([{
        'part_id': str(part_id),
        'unit_price': unit_price,
        'workshop_service_count': 35,
        'intermittent_ratio': 0.1,
        'mean_demand_interval': 1.5,
        'day_of_week': day_of_week,
        'day_of_month': today.day,
        'month': month,
        'quarter': (month - 1) // 3 + 1,
        'is_weekend': 1 if day_of_week in [5, 6] else 0,
        'sin_day_of_week': sin_day_of_week,
        'cos_day_of_week': cos_day_of_week,
        'sin_month': sin_month,
        'cos_month': cos_month,
        'price_to_mean_ratio': 1.0,
        'qty_lag_1': 22,
        'qty_lag_7': 28,
        'qty_lag_14': 25,
        'qty_lag_30': 30,
        'qty_roll_mean_7': 24.5,
        'qty_roll_std_7': 3.2,
        'qty_roll_max_7': 35.0,
        'qty_roll_mean_14': 25.1,
        'qty_roll_std_14': 3.8,
        'qty_roll_max_14': 38.0,
        'qty_roll_mean_30': 26.1,
        'qty_roll_std_30': 4.1,
        'qty_roll_max_30': 42.0
    }])

    prediction = pipeline.predict(input_data)[0]
    
    return jsonify({
        'part_id': part_id,
        'predicted_quantity': int(max(0, round(prediction))),
        'confidence_interval': [int(max(0, round(prediction * 0.9))), int(round(prediction * 1.1))],
        'timestamp': datetime.datetime.now().isoformat()
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)
"""Compatibility launcher for the project-level Python application.

This uploaded duplicate used to load a pickle on every prediction request.
Run the maintained server from the project root instead.
"""
if __name__ == '__main__':
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    from app import app
    app.run(host='127.0.0.1', port=5000, debug=False)
    raise SystemExit

raise RuntimeError('This duplicate entry point is retired. Run the project-level app.py instead.')

