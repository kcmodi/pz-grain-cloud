import os
import json
import random
import traceback
import re
from collections import defaultdict
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from google import genai
from flask import send_file
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import io
import psycopg2
from psycopg2 import pool

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'pz-grain-secret-key-2026')

# =====================================================================
# --- ENTERPRISE CLOUD DATABASE CONFIGURATION (POSTGRESQL) ---
# =====================================================================
# Render injects DATABASE_URL automatically via Environment Variables
DB_URL = os.environ.get("DATABASE_URL")

# Create a massive Connection Pool to handle 20+ simultaneous terminals
try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(minconn=5, maxconn=50, dsn=DB_URL)
    print("✅ Enterprise PostgreSQL Connection Pool Initialized.")
except Exception as e:
    print(f"❌ Database Connection Failed: Ensure PostgreSQL is running. Error: {e}")
    db_pool = None

# --- SQL TRANSLATION WRAPPER ---
# Converts old MS Access syntax to PostgreSQL syntax seamlessly
class CursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, query, params=None):
        match = re.search(r'SELECT TOP (\d+)', query, flags=re.IGNORECASE)
        if match:
            limit_val = match.group(1)
            query = re.sub(r'SELECT TOP \d+', 'SELECT', query, flags=re.IGNORECASE)
            query += f" LIMIT {limit_val}"
            
        query = query.replace('?', '%s')
        
        if "@@IDENTITY" in query:
            query = query.replace("@@IDENTITY", "LASTVAL()")
            
        if params:
            self.cursor.execute(query, params)
        else:
            self.cursor.execute(query)

    def fetchone(self): return self.cursor.fetchone()
    def fetchall(self): return self.cursor.fetchall()

class DBWrapper:
    def __init__(self, conn):
        self.conn = conn
    def cursor(self):
        return CursorWrapper(self.conn.cursor())
    def commit(self): self.conn.commit()
    def rollback(self): self.conn.rollback()
    def close(self):
        if self.conn and db_pool: db_pool.putconn(self.conn)

def get_db_connection():
    if not db_pool: raise Exception("Database Pool Not Initialized")
    return DBWrapper(db_pool.getconn())

def parse_full_address(data, prefix=''):
    apt = data.get(f'{prefix}apt', '').strip()
    area = data.get(f'{prefix}area', '').strip()
    city = data.get(f'{prefix}city', '').strip()
    state = data.get(f'{prefix}state', '').strip()
    zipcode = data.get(f'{prefix}zipcode', '').strip()
    parts = [apt, area, city, state, zipcode]
    full_addr = ", ".join([p for p in parts if p])
    if not full_addr and 'street' in data:
        full_addr = f"{data.get('street', '')}, {data.get('city', '')}".strip(', ')
    elif not full_addr and 'seller_address' in data:
        full_addr = data.get('seller_address', '')
    return full_addr

# --- AUTO-HEALING DATABASE ENGINE (POSTGRESQL NATIVE) ---
def init_db_updates():
    if not db_pool: return
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("CREATE TABLE IF NOT EXISTS Contracts (ContractID SERIAL PRIMARY KEY, CustomerCode VARCHAR(50), ProductCode VARCHAR(50), AgreedQty NUMERIC, FixedRate NUMERIC, FulfillmentStatus VARCHAR(50), CreatedAt TIMESTAMP)")
        cursor.execute("CREATE TABLE IF NOT EXISTS AreaMaster (AreaID SERIAL PRIMARY KEY, AreaName VARCHAR(100) UNIQUE, Description VARCHAR(200))")
        cursor.execute("CREATE TABLE IF NOT EXISTS Customers (CustomerID SERIAL PRIMARY KEY, CustomerCode VARCHAR(50), CustomerName VARCHAR(100), Phone VARCHAR(50), Email VARCHAR(100), Address TEXT, AreaID INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS OrderHistory (HistoryID SERIAL PRIMARY KEY, SaleID INTEGER, ModificationDate TIMESTAMP, PreviousTotal NUMERIC, PreviousItems TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS SalesOrders (SaleID SERIAL PRIMARY KEY, CustomerID INTEGER, SaleDate TIMESTAMP, InvoiceNo VARCHAR(50), TotalAmount NUMERIC, TaxAmount NUMERIC, PaymentMethod VARCHAR(50), PaymentStatus VARCHAR(50), FulfillmentMode VARCHAR(50) DEFAULT 'Takeaway', Branch VARCHAR(10), CreatedBy VARCHAR(50))")
        cursor.execute("CREATE TABLE IF NOT EXISTS SalesDetails (SaleDetailID SERIAL PRIMARY KEY, SaleID INTEGER, ProductID INTEGER, Quantity NUMERIC, UnitPrice NUMERIC, Total NUMERIC)")
        cursor.execute("CREATE TABLE IF NOT EXISTS Products (ProductID SERIAL PRIMARY KEY, ProductCode VARCHAR(50), ProductName VARCHAR(100), PurchasePrice NUMERIC, SalesPrice NUMERIC, TaxRate NUMERIC, UnitPrice NUMERIC, Unit VARCHAR(20), StockQuantity NUMERIC)")
        cursor.execute("CREATE TABLE IF NOT EXISTS Suppliers (SupplierID SERIAL PRIMARY KEY, SupplierCode VARCHAR(50), SupplierName VARCHAR(100), Phone VARCHAR(50), Address TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS PurchaseOrders (PurchaseID SERIAL PRIMARY KEY, SupplierID INTEGER, PurchaseDate TIMESTAMP, InvoiceNo VARCHAR(50), TotalAmount NUMERIC, PaymentStatus VARCHAR(50), BrokerName VARCHAR(100), Branch VARCHAR(10), CreatedBy VARCHAR(50))")
        cursor.execute("CREATE TABLE IF NOT EXISTS PurchaseDetails (PurchaseDetailID SERIAL PRIMARY KEY, PurchaseID INTEGER, ProductID INTEGER, Quantity NUMERIC, UnitPrice NUMERIC, Total NUMERIC)")
        cursor.execute("CREATE TABLE IF NOT EXISTS Employees (EmployeeID SERIAL PRIMARY KEY, FirstName VARCHAR(50), LastName VARCHAR(50), Position VARCHAR(50), Phone VARCHAR(50), Email VARCHAR(100), Username VARCHAR(50), Branch VARCHAR(10))")
        cursor.execute("CREATE TABLE IF NOT EXISTS SystemUsers (id SERIAL PRIMARY KEY, username VARCHAR(50), password_plain VARCHAR(50))")
        cursor.execute("CREATE TABLE IF NOT EXISTS Approvals (RequestID SERIAL PRIMARY KEY, ReqCode VARCHAR(50), Category VARCHAR(100), Details TEXT, ReqValue NUMERIC, Status VARCHAR(50), CreatedAt TIMESTAMP)")
        cursor.execute("CREATE TABLE IF NOT EXISTS Expenses (ExpenseID SERIAL PRIMARY KEY, ExpDate TIMESTAMP, Category VARCHAR(100), Amount NUMERIC, Notes TEXT, Branch VARCHAR(10))")
        cursor.execute("CREATE TABLE IF NOT EXISTS WriteOffs (LogID SERIAL PRIMARY KEY, ProductCode VARCHAR(50), Qty NUMERIC, Reason VARCHAR(100), LogDate TIMESTAMP, LoggedBy VARCHAR(50))")
        cursor.execute("CREATE TABLE IF NOT EXISTS Payments (PaymentID SERIAL PRIMARY KEY, SaleID INTEGER, PaymentDate TIMESTAMP, Amount NUMERIC, Method VARCHAR(50))")
        conn.commit()

        cursor.execute("SELECT id FROM SystemUsers WHERE username = 'admin'")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO SystemUsers (username, password_plain) VALUES ('admin', 'admin')")
            cursor.execute("INSERT INTO Employees (FirstName, LastName, Position, Phone, Email, Username, Branch) VALUES ('Master', 'Admin', 'Owner', 'N/A', 'admin@pz.com', 'admin', 'ALL')")
            conn.commit()

    except Exception as e:
        print(f"⚠️ DB Auto-Heal Engine Error: {e}")
    finally:
        if conn: conn.close()

init_db_updates()


# --- AUTH ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT u.id, u.password_plain, e.Position, e.Branch FROM SystemUsers u LEFT JOIN Employees e ON u.username = e.Username WHERE u.username = %s",
                (username,))
            user_row = cursor.fetchone()
            if user_row and user_row[1] == password:
                session['logged_in'] = True
                session['username'] = username
                session['role'] = user_row[2].strip() if len(user_row) > 2 and user_row[2] else 'Staff'
                session['branch'] = user_row[3].strip() if len(user_row) > 3 and user_row[3] else 'NV'
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error="Invalid Credentials.")
        except Exception as e:
            return render_template('login.html', error="Database fault occurred.")
        finally:
            if conn: conn.close()
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# --- MASTER DASHBOARD ---
@app.route('/')
def index():
    if not session.get('logged_in'): return redirect(url_for('login'))
    conn = None
    
    user_branch = session.get('branch', 'NV')
    user_role = session.get('role', 'Staff').lower()
    is_owner = (user_role == 'owner' or user_branch == 'ALL')
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT ProductCode, ProductName, SalesPrice, PurchasePrice, UnitPrice, Unit, StockQuantity, TaxRate FROM Products ORDER BY ProductCode ASC")
        grains = []
        for r in cursor.fetchall():
            s_price = float(r[2] if r[2] is not None else (r[4] if r[4] is not None else 0))
            p_price = float(r[3] if r[3] is not None else (r[4] if r[4] is not None else 0))
            grains.append({
                "code": r[0], "name": r[1], "sales_price": s_price, "purchase_price": p_price, "unit": r[5],
                "stock_kg": float(r[6] if r[6] is not None else 0), "price_per_kg": s_price,
                "tax_rate": float(r[7] if r[7] is not None else 0)
            })

        if is_owner:
            cursor.execute("SELECT o.SaleID, c.CustomerName, c.Phone, c.Address, o.TotalAmount, c.CustomerCode, o.PaymentMethod, o.InvoiceNo, c.Email, o.FulfillmentMode, o.TaxAmount, o.CreatedBy, o.Branch FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID ORDER BY o.SaleID DESC")
        else:
            cursor.execute("SELECT o.SaleID, c.CustomerName, c.Phone, c.Address, o.TotalAmount, c.CustomerCode, o.PaymentMethod, o.InvoiceNo, c.Email, o.FulfillmentMode, o.TaxAmount, o.CreatedBy, o.Branch FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID WHERE o.Branch = %s ORDER BY o.SaleID DESC", (user_branch,))
        
        orders = []
        for row in cursor.fetchall():
            sale_id = row[0]
            cursor.execute("SELECT p.ProductCode, p.ProductName, d.Quantity, d.UnitPrice FROM SalesDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.SaleID = %s ORDER BY d.SaleDetailID ASC", (sale_id,))
            items = [{"code": d[0], "name": d[1], "qty": float(d[2] if d[2] else 0), "price": float(d[3] if d[3] else 0)} for d in cursor.fetchall()]

            cursor.execute("SELECT ModificationDate, PreviousTotal, PreviousItems FROM OrderHistory WHERE SaleID = %s ORDER BY ModificationDate DESC", (sale_id,))
            history = []
            for h in cursor.fetchall():
                try: parsed_items = json.loads(h[2]) if h[2] else []
                except: parsed_items = []
                history.append({"date": h[0].strftime('%Y-%m-%d %I:%M %p') if h[0] else 'N/A', "old_total": float(h[1] if h[1] is not None else 0.0), "old_items": parsed_items})

            orders.append({
                "id": sale_id, "customer_name": row[1], "customer_phone": row[2] or "", "customer_address": row[3] or "",
                "items_raw": items, "total_amount": float(row[4] if row[4] is not None else 0.0),
                "customer_code": row[5], "payment_method": row[6], "invoice_no": row[7] or "",
                "customer_email": row[8] or "", "fulfillment_mode": row[9] or "Takeaway",
                "tax_amount": float(row[10] if len(row) > 10 and row[10] else 0), 
                "created_by": row[11] or "System", "branch": row[12] or "NV",
                "history": history
            })

        if is_owner:
            cursor.execute("SELECT o.PurchaseID, s.SupplierName, s.Phone, s.Address, o.TotalAmount, o.PurchaseDate, o.InvoiceNo, s.SupplierCode, o.BrokerName, o.CreatedBy, o.Branch FROM PurchaseOrders o INNER JOIN Suppliers s ON o.SupplierID = s.SupplierID ORDER BY o.PurchaseID DESC")
        else:
            cursor.execute("SELECT o.PurchaseID, s.SupplierName, s.Phone, s.Address, o.TotalAmount, o.PurchaseDate, o.InvoiceNo, s.SupplierCode, o.BrokerName, o.CreatedBy, o.Branch FROM PurchaseOrders o INNER JOIN Suppliers s ON o.SupplierID = s.SupplierID WHERE o.Branch = %s ORDER BY o.PurchaseID DESC", (user_branch,))
        
        purchases = []
        for row in cursor.fetchall():
            pur_id = row[0]
            cursor.execute("SELECT p.ProductCode, p.ProductName, d.Quantity, d.UnitPrice FROM PurchaseDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.PurchaseID = %s ORDER BY d.PurchaseDetailID ASC", (pur_id,))
            items = [{"code": d[0], "name": d[1], "qty": float(d[2] if d[2] else 0), "price": float(d[3] if d[3] else 0)} for d in cursor.fetchall()]
            purchases.append({
                "id": pur_id, "seller_name": row[1], "seller_phone": row[2] or "", "seller_address": row[3] or "",
                "items_raw": items, "total_cost": float(row[4] if row[4] else 0),
                "date": row[5].strftime('%d-%b-%Y') if row[5] else 'N/A', "invoice_no": row[6] or "",
                "seller_code": row[7] or "", "broker_name": row[8] or "",
                "created_by": row[9] or "System", "branch": row[10] or "NV"
            })

        cursor.execute("SELECT CustomerCode, CustomerName, Phone, Email, Address, AreaID FROM Customers ORDER BY CustomerName ASC")
        customers = [{"code": r[0], "name": r[1], "phone": r[2] or "", "email": r[3] or "", "address": r[4] or "", "area_id": r[5]} for r in cursor.fetchall()]

        cursor.execute("SELECT SupplierCode, SupplierName, Phone, Address FROM Suppliers ORDER BY SupplierName ASC")
        suppliers = [{"code": r[0], "name": r[1], "phone": r[2] or "", "address": r[3] or ""} for r in cursor.fetchall()]

        if is_owner:
            cursor.execute("SELECT EmployeeID, FirstName, LastName, Position, Username, Phone, Branch FROM Employees ORDER BY EmployeeID ASC")
            employees = [{"code": f"EMP-{r[0]}", "name": f"{r[1]} {r[2]}".strip(), "role": r[3], "username": r[4] or "", "phone": r[5] or "", "branch": r[6] or "NV"} for r in cursor.fetchall()]
        else:
            cursor.execute("SELECT EmployeeID, FirstName, LastName, Position, Username, Phone, Branch FROM Employees WHERE Branch = %s ORDER BY EmployeeID ASC", (user_branch,))
            employees = [{"code": f"EMP-{r[0]}", "name": f"{r[1]} {r[2]}".strip(), "role": r[3], "username": r[4] or "", "phone": r[5] or "", "branch": r[6] or "NV"} for r in cursor.fetchall()]

        if is_owner:
            cursor.execute("SELECT p.PaymentID, p.SaleID, p.PaymentDate, p.Amount, p.Method FROM Payments p ORDER BY p.PaymentID DESC")
        else:
            cursor.execute("SELECT p.PaymentID, p.SaleID, p.PaymentDate, p.Amount, p.Method FROM Payments p INNER JOIN SalesOrders o ON p.SaleID = o.SaleID WHERE o.Branch = %s ORDER BY p.PaymentID DESC", (user_branch,))
        payments = [{"ref": f"ORD-{r[1]}", "flow": "IN", "amount": float(r[3] if r[3] else 0), "method": r[4] or "Cash", "date": r[2].strftime('%Y-%m-%d %H:%M') if r[2] else 'N/A'} for r in cursor.fetchall()]

        if is_owner:
            cursor.execute("SELECT ExpDate, Category, Amount, Notes FROM Expenses ORDER BY ExpDate DESC")
        else:
            cursor.execute("SELECT ExpDate, Category, Amount, Notes FROM Expenses WHERE Branch = %s ORDER BY ExpDate DESC", (user_branch,))
        expenses = [{"date": r[0].strftime('%Y-%m-%d'), "category": r[1], "amount": float(r[2] if r[2] else 0), "notes": r[3] or ""} for r in cursor.fetchall()]

        try:
            cursor.execute("SELECT AreaID, AreaName, Description FROM AreaMaster ORDER BY AreaName ASC")
            areas = [{"id": r[0], "name": r[1], "desc": r[2] or ""} for r in cursor.fetchall()]
        except:
            areas = []

        if is_owner:
            cursor.execute("SELECT c.CustomerName, c.Phone, o.SaleDate, o.TotalAmount FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID WHERE o.PaymentStatus = 'Pending'")
        else:
            cursor.execute("SELECT c.CustomerName, c.Phone, o.SaleDate, o.TotalAmount FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID WHERE o.PaymentStatus = 'Pending' AND o.Branch = %s", (user_branch,))
        
        ar_dict = defaultdict(lambda: {"0_30": 0, "31_60": 0, "61_90": 0, "90_plus": 0, "total": 0, "phone": ""})
        for r in cursor.fetchall():
            name, phone, sdate, amt = r[0], r[1], r[2], float(r[3] or 0)
            if isinstance(sdate, str):
                try: sdate = datetime.strptime(sdate.split('.')[0], '%Y-%m-%d %H:%M:%S')
                except: sdate = datetime.now()
            days = (datetime.now() - sdate).days if isinstance(sdate, datetime) else 0
            ar_dict[name]["phone"] = phone or "N/A"
            ar_dict[name]["total"] += amt
            if days <= 30: ar_dict[name]["0_30"] += amt
            elif days <= 60: ar_dict[name]["31_60"] += amt
            elif days <= 90: ar_dict[name]["61_90"] += amt
            else: ar_dict[name]["90_plus"] += amt
        ar_aging = [{"name": k, **v} for k, v in ar_dict.items()]
        ar_aging.sort(key=lambda x: x["total"], reverse=True)

        if is_owner:
            cursor.execute("SELECT SUM(sd.Quantity * p.PurchasePrice) FROM SalesDetails sd INNER JOIN Products p ON sd.ProductID = p.ProductID")
        else:
            cursor.execute("SELECT SUM(sd.Quantity * p.PurchasePrice) FROM SalesDetails sd INNER JOIN Products p ON sd.ProductID = p.ProductID INNER JOIN SalesOrders o ON sd.SaleID = o.SaleID WHERE o.Branch = %s", (user_branch,))
        cogs_val = cursor.fetchone()
        total_cogs = float(cogs_val[0] if cogs_val and cogs_val[0] else 0.0)

        return render_template('pos.html', grains=grains, orders=orders, purchases=purchases, customers=customers,
                               suppliers=suppliers, employees=employees, payments=payments, movements=[],
                               expenses=expenses, ar_aging=ar_aging, total_cogs=total_cogs, areas=areas, approvals=[])

    except Exception as e:
        error_details = traceback.format_exc()
        return f"<div style='font-family: monospace; padding: 2rem; background: #fff1f2; color: #9f1239; border-radius: 12px; margin: 20px;'><h1>Dashboard Initialization Crash</h1><p><b>Error:</b> {str(e)}</p><pre>{error_details}</pre></div>", 500
    finally:
        if conn: conn.close()


# --- TRANSACTION ENDPOINTS ---
@app.route('/checkout', methods=['POST'])
def checkout():
    if not session.get('logged_in'): return jsonify({"success": False, "message": "Unauthorized"}), 401
    data = request.get_json()
    now = datetime.now()
    
    user = session.get('username', 'System')
    branch = session.get('branch', 'NV')
    invoice_no = f"{branch}{now.strftime('%m%d')}-{random.randint(100, 999)}"

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        requested_volumes = defaultdict(float)
        for item in data['items']: requested_volumes[item['code']] += float(item['qty'])
        for code, total_qty in requested_volumes.items():
            cursor.execute("SELECT StockQuantity FROM Products WHERE ProductCode = %s", (code,))
            row = cursor.fetchone()
            if not row or float(row[0]) < total_qty:
                return jsonify({"success": False, "message": f"Insufficient stock for variety code [{code}]"}), 400

        full_addr = parse_full_address(data)
        phone = (data.get('customer_phone') or '').strip()
        email = (data.get('customer_email') or '').strip()
        req_code = (data.get('customer_code') or '').strip()
        fulfillment_mode = data.get('fulfillment_mode', 'Takeaway')
        tax_amount = data.get('tax_amount', 0.0)
        area_id = data.get('area_id', None)

        cust_id, final_code = None, req_code

        if req_code == 'WALKIN':
            cursor.execute("SELECT CustomerID FROM Customers WHERE CustomerCode = 'WALKIN'")
            walkin_row = cursor.fetchone()
            if walkin_row: cust_id = int(walkin_row[0])
            else:
                cursor.execute("INSERT INTO Customers (CustomerCode, CustomerName, Phone, Address) VALUES ('WALKIN', 'Walk-in Customer', '0000000000', '')")
                cursor.execute("SELECT LASTVAL()")
                cust_id = int(cursor.fetchone()[0])
            final_code = 'WALKIN'
        else:
            if req_code and req_code.lower() != "new account":
                cursor.execute("SELECT CustomerID, AreaID FROM Customers WHERE CustomerCode = %s", (req_code,))
                cust_row = cursor.fetchone()
                if cust_row:
                    cust_id = int(cust_row[0])
                    final_area = area_id if 'area_id' in data else cust_row[1]
                    cursor.execute("UPDATE Customers SET Address = %s, Email = %s, CustomerName = %s, Phone = %s, AreaID = %s WHERE CustomerID = %s", (full_addr, email, data.get('customer_name', ''), phone, final_area, cust_id))

            if not cust_id:
                cursor.execute("SELECT CustomerID, CustomerCode, AreaID FROM Customers WHERE Phone = %s", (phone,))
                cust_row = cursor.fetchone()
                if cust_row:
                    cust_id = int(cust_row[0])
                    final_code = cust_row[1]
                    final_area = area_id if 'area_id' in data else cust_row[2]
                    cursor.execute("UPDATE Customers SET Address = %s, Email = %s, CustomerName = %s, AreaID = %s WHERE CustomerID = %s", (full_addr, email, data.get('customer_name', ''), final_area, cust_id))
                else:
                    while True:
                        final_code = str(random.randint(100000, 999999))
                        cursor.execute("SELECT CustomerID FROM Customers WHERE CustomerCode = %s", (final_code,))
                        if not cursor.fetchone(): break
                    cursor.execute("INSERT INTO Customers (CustomerCode, CustomerName, Phone, Email, AreaID, Address) VALUES (%s, %s, %s, %s, %s, %s)", (final_code, data.get('customer_name', ''), phone, email, area_id, full_addr))
                    cursor.execute("SELECT LASTVAL()")
                    cust_id = int(cursor.fetchone()[0])

        method = data.get('payment_method', 'Cash')
        split_payments = data.get('split_payments')
        status = 'Pending' if method in ['Pay Later', 'Cash on Delivery'] else 'Paid'

        cursor.execute(
            "INSERT INTO SalesOrders (CustomerID, SaleDate, InvoiceNo, TotalAmount, TaxAmount, PaymentMethod, PaymentStatus, FulfillmentMode, Branch, CreatedBy) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (cust_id, now, invoice_no, data['total'], tax_amount, method, status, fulfillment_mode, branch, user))
        cursor.execute("SELECT LASTVAL()")
        sale_id = int(cursor.fetchone()[0])

        for item in data['items']:
            cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = %s", (item['code'],))
            prod_row = cursor.fetchone()
            if not prod_row:
                conn.rollback()
                return jsonify({"success": False, "message": f"Invalid Product Code: {item['code']}"}), 400
            prod_id = int(prod_row[0])
            cursor.execute("UPDATE Products SET StockQuantity = StockQuantity - %s WHERE ProductID = %s", (float(item['qty']), prod_id))
            cursor.execute("INSERT INTO SalesDetails (SaleID, ProductID, Quantity, UnitPrice, Total) VALUES (%s, %s, %s, %s, %s)", (sale_id, prod_id, float(item['qty']), float(item['price']), float(item['qty']) * float(item['price'])))

        if status == 'Paid':
            if split_payments:
                for sp in split_payments:
                    cursor.execute("INSERT INTO Payments (SaleID, PaymentDate, Amount, Method) VALUES (%s, %s, %s, %s)", (sale_id, now, sp['amount'], sp['method']))
            else:
                cursor.execute("INSERT INTO Payments (SaleID, PaymentDate, Amount, Method) VALUES (%s, %s, %s, %s)", (sale_id, now, data['total'], method))

        conn.commit()
        return jsonify({"success": True, "order_id": sale_id, "customer_code": final_code, "invoice_no": invoice_no})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/edit-checkout', methods=['POST'])
def edit_checkout():
    if not session.get('logged_in'): return jsonify({"success": False, "message": "Unauthorized"}), 401
    data = request.get_json()
    order_id = int(data.get('order_id', 0))
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT TotalAmount FROM SalesOrders WHERE SaleID = %s", (order_id,))
        old_total_fetch = cursor.fetchone()
        old_total = float(old_total_fetch[0] if old_total_fetch and old_total_fetch[0] is not None else 0.0)

        cursor.execute("SELECT p.ProductCode, p.ProductName, d.Quantity, d.UnitPrice FROM SalesDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.SaleID = %s", (order_id,))
        old_items = [{"code": r[0], "name": r[1], "qty": float(r[2]), "price": float(r[3])} for r in cursor.fetchall()]

        cursor.execute("INSERT INTO OrderHistory (SaleID, ModificationDate, PreviousTotal, PreviousItems) VALUES (%s, %s, %s, %s)", (order_id, datetime.now(), old_total, json.dumps(old_items)))

        for item in old_items:
            cursor.execute("UPDATE Products SET StockQuantity = StockQuantity + %s WHERE ProductCode = %s", (item['qty'], item['code']))

        requested_volumes = defaultdict(float)
        for item in data['items']: requested_volumes[item['code']] += float(item['qty'])
        for code, total_qty in requested_volumes.items():
            cursor.execute("SELECT StockQuantity FROM Products WHERE ProductCode = %s", (code,))
            row = cursor.fetchone()
            if not row or float(row[0]) < total_qty:
                conn.rollback()
                return jsonify({"success": False, "message": f"Modification rejected: Insufficient stock for [{code}]"}), 400

        cursor.execute("DELETE FROM SalesDetails WHERE SaleID = %s", (order_id,))
        for item in data['items']:
            cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = %s", (item['code'],))
            prod_row = cursor.fetchone()
            if not prod_row:
                conn.rollback()
                return jsonify({"success": False, "message": f"Invalid Product Code: {item['code']}"}), 400
            prod_id = int(prod_row[0])
            cursor.execute("UPDATE Products SET StockQuantity = StockQuantity - %s WHERE ProductID = %s", (float(item['qty']), prod_id))
            cursor.execute("INSERT INTO SalesDetails (SaleID, ProductID, Quantity, UnitPrice, Total) VALUES (%s, %s, %s, %s, %s)", (order_id, prod_id, float(item['qty']), float(item['price']), float(item['qty']) * float(item['price'])))

        cursor.execute("SELECT CustomerID FROM SalesOrders WHERE SaleID = %s", (order_id,))
        cust_id = int(cursor.fetchone()[0])
        new_method = data.get('payment_method', 'Cash')
        split_payments = data.get('split_payments')
        fulfillment_mode = data.get('fulfillment_mode', 'Takeaway')
        new_status = 'Pending' if new_method in ['Pay Later', 'Cash on Delivery'] else 'Paid'

        if 'area_id' in data:
            cursor.execute("UPDATE Customers SET CustomerName = %s, Phone = %s, Address = %s, AreaID = %s WHERE CustomerID = %s", (data.get('customer_name'), data.get('customer_phone'), data.get('customer_address', ''), data.get('area_id'), cust_id))
        else:
            cursor.execute("UPDATE Customers SET CustomerName = %s, Phone = %s, Address = %s WHERE CustomerID = %s", (data.get('customer_name'), data.get('customer_phone'), data.get('customer_address', ''), cust_id))

        cursor.execute("UPDATE SalesOrders SET TotalAmount = %s, TaxAmount = %s, PaymentMethod = %s, PaymentStatus = %s, FulfillmentMode = %s WHERE SaleID = %s", (data['total'], data.get('tax_amount', 0.0), new_method, new_status, fulfillment_mode, order_id))

        cursor.execute("DELETE FROM Payments WHERE SaleID = %s", (order_id,))
        if new_status == 'Paid':
            if split_payments:
                for sp in split_payments: cursor.execute("INSERT INTO Payments (SaleID, PaymentDate, Amount, Method) VALUES (%s, %s, %s, %s)", (order_id, datetime.now(), sp['amount'], sp['method']))
            else:
                cursor.execute("INSERT INTO Payments (SaleID, PaymentDate, Amount, Method) VALUES (%s, %s, %s, %s)", (order_id, datetime.now(), data['total'], new_method))

        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except Exception: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/submit-purchase', methods=['POST'])
def submit_purchase():
    if not session.get('logged_in'): return jsonify({"success": False, "message": "Unauthorized"}), 401
    data = request.get_json()
    now = datetime.now()

    user = session.get('username', 'System')
    branch = session.get('branch', 'NV')
    invoice_no = f"P-{branch}{now.strftime('%m%d')}-{random.randint(100, 999)}"

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        full_addr = parse_full_address(data, prefix='seller_')
        phone = (data.get('seller_phone') or '').strip()
        req_code = (data.get('seller_code') or '').strip()
        sup_id = None

        if req_code and req_code.lower() not in ["", "auto-generated", "new account"]:
            cursor.execute("SELECT SupplierID FROM Suppliers WHERE SupplierCode = %s", (req_code,))
            sup_row = cursor.fetchone()
            if sup_row:
                sup_id = int(sup_row[0])
                cursor.execute("UPDATE Suppliers SET SupplierName = %s, Phone = %s, Address = %s WHERE SupplierID = %s", (data.get('seller_name', ''), phone, full_addr, sup_id))

        if not sup_id:
            cursor.execute("SELECT SupplierID FROM Suppliers WHERE Phone = %s", (phone,))
            sup_row = cursor.fetchone()
            if sup_row:
                sup_id = int(sup_row[0])
                cursor.execute("UPDATE Suppliers SET SupplierName = %s, Address = %s WHERE SupplierID = %s", (data.get('seller_name', ''), full_addr, sup_id))
            else:
                while True:
                    sup_code = str(random.randint(10000, 999999))
                    cursor.execute("SELECT SupplierID FROM Suppliers WHERE SupplierCode = %s", (sup_code,))
                    if not cursor.fetchone(): break
                cursor.execute("INSERT INTO Suppliers (SupplierCode, SupplierName, Phone, Address) VALUES (%s, %s, %s, %s)", (sup_code, data.get('seller_name', ''), phone, full_addr))
                cursor.execute("SELECT LASTVAL()")
                sup_id = int(cursor.fetchone()[0])

        broker = data.get('broker_name', '').strip()
        cursor.execute(
            "INSERT INTO PurchaseOrders (SupplierID, PurchaseDate, InvoiceNo, TotalAmount, PaymentStatus, BrokerName, Branch, CreatedBy) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (sup_id, now, invoice_no, data['total_cost'], 'Paid', broker, branch, user))
        cursor.execute("SELECT LASTVAL()")
        pur_id = int(cursor.fetchone()[0])

        for item in data['items']:
            cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = %s", (item['code'],))
            prod_row = cursor.fetchone()
            if not prod_row:
                conn.rollback()
                return jsonify({"success": False, "message": f"Invalid Product Code: {item['code']}"}), 400
            prod_id = int(prod_row[0])
            cursor.execute("UPDATE Products SET StockQuantity = StockQuantity + %s WHERE ProductID = %s", (float(item['qty']), prod_id))
            cursor.execute("INSERT INTO PurchaseDetails (PurchaseID, ProductID, Quantity, UnitPrice, Total) VALUES (%s, %s, %s, %s, %s)", (pur_id, prod_id, float(item['qty']), float(item['price']), float(item['qty']) * float(item['price'])))

        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/edit-purchase', methods=['POST'])
def edit_purchase():
    if not session.get('logged_in'): return jsonify({"success": False, "message": "Unauthorized"}), 401
    data = request.get_json()
    pur_id = int(data.get('purchase_id', 0))
    conn = None
    try:
        if not pur_id: return jsonify({"success": False, "message": "Invalid Purchase ID."}), 400
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT p.ProductCode, p.ProductName, d.Quantity, d.UnitPrice FROM PurchaseDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.PurchaseID = %s", (pur_id,))
        old_items = [{"code": r[0], "qty": float(r[2])} for r in cursor.fetchall()]

        for item in old_items:
            cursor.execute("UPDATE Products SET StockQuantity = StockQuantity - %s WHERE ProductCode = %s", (item['qty'], item['code']))

        cursor.execute("DELETE FROM PurchaseDetails WHERE PurchaseID = %s", (pur_id,))

        for item in data['items']:
            cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = %s", (item['code'],))
            prod_row = cursor.fetchone()
            if not prod_row:
                conn.rollback()
                return jsonify({"success": False, "message": f"Invalid Product Code: {item['code']}"}), 400
            prod_id = int(prod_row[0])
            cursor.execute("UPDATE Products SET StockQuantity = StockQuantity + %s WHERE ProductID = %s", (float(item['qty']), prod_id))
            item_total = float(item['qty']) * float(item['price'])
            cursor.execute("INSERT INTO PurchaseDetails (PurchaseID, ProductID, Quantity, UnitPrice, Total) VALUES (%s, %s, %s, %s, %s)", (pur_id, prod_id, float(item['qty']), float(item['price']), item_total))

        cursor.execute("SELECT SupplierID FROM PurchaseOrders WHERE PurchaseID = %s", (pur_id,))
        sup_id = int(cursor.fetchone()[0])
        cursor.execute("UPDATE Suppliers SET SupplierName = %s, Phone = %s, Address = %s WHERE SupplierID = %s", (data.get('seller_name'), data.get('seller_phone'), data.get('seller_address'), sup_id))

        broker = data.get('broker_name', '').strip()
        cursor.execute("UPDATE PurchaseOrders SET TotalAmount = %s, BrokerName = %s WHERE PurchaseID = %s", (data['total'], broker, pur_id))

        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except Exception: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

# --- FINANCIAL ACCURACY & INVENTORY MODULES ---
@app.route('/api/save-product', methods=['POST'])
def save_product():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    user_role = session.get('role', 'Staff').lower()
    is_management = user_role in ['admin', 'manager', 'owner']
    data = request.get_json()
    orig_code = data.get('original_code', '').strip()
    code = data.get('code', '').strip()
    name = data.get('name', '').strip()
    sal_price = float(data.get('sales_price', 0))
    tax_rate = float(data.get('tax_rate', 0))
    pur_price = float(data.get('purchase_price', 0)) if is_management else None

    if not code or not name: return jsonify({"success": False, "message": "Code and Name are required."})
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if orig_code:
            if orig_code != code:
                cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = %s", (code,))
                if cursor.fetchone(): return jsonify({"success": False, "message": "Target Product Code already exists!"})
            if not is_management:
                cursor.execute("SELECT PurchasePrice FROM Products WHERE ProductCode = %s", (orig_code,))
                existing_pur = cursor.fetchone()
                pur_price = float(existing_pur[0]) if existing_pur and existing_pur[0] is not None else 0.0

            cursor.execute("UPDATE Products SET ProductCode=%s, ProductName=%s, PurchasePrice=%s, SalesPrice=%s, TaxRate=%s WHERE ProductCode=%s", (code, name, pur_price, sal_price, tax_rate, orig_code))
            msg = "Item updated successfully!"
        else:
            cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = %s", (code,))
            if cursor.fetchone(): return jsonify({"success": False, "message": "Product Code already exists!"})
            if not is_management: pur_price = 0.0
            cursor.execute("INSERT INTO Products (ProductCode, ProductName, PurchasePrice, SalesPrice, TaxRate, StockQuantity, Unit) VALUES (%s, %s, %s, %s, %s, %s, %s)", (code, name, pur_price, sal_price, tax_rate, 0, 'kg'))
            msg = "New item added successfully!"
        conn.commit()
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/update-stock', methods=['POST'])
def update_stock():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    data = request.get_json()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE Products SET StockQuantity = %s WHERE ProductCode = %s", (float(data['new_stock']), data['code']))
        conn.commit()
        return jsonify({"success": True, "message": "Warehouse stock modified."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/add-expense', methods=['POST'])
def add_expense():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    if session.get('role', 'Staff').lower() not in ['admin', 'manager', 'owner']:
        return jsonify({"success": False, "message": "Access Denied."}), 403

    branch = session.get('branch', 'NV')
    data = request.get_json()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO Expenses (ExpDate, Category, Amount, Notes, Branch) VALUES (%s, %s, %s, %s, %s)",
                       (datetime.now(), data.get('category'), float(data.get('amount', 0)), data.get('notes', ''), branch))
        conn.commit()
        return jsonify({"success": True, "message": "Expense Logged Successfully."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/write-off-stock', methods=['POST'])
def write_off_stock():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    if session.get('role', 'Staff').lower() not in ['admin', 'manager', 'owner']:
        return jsonify({"success": False, "message": "Access Denied."}), 403

    data = request.get_json()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT StockQuantity FROM Products WHERE ProductCode = %s", (data['code'],))
        row = cursor.fetchone()
        if not row or float(row[0]) < float(data['qty']):
            return jsonify({"success": False, "message": "Cannot write-off more stock than is available."}), 400

        cursor.execute("UPDATE Products SET StockQuantity = StockQuantity - %s WHERE ProductCode = %s", (float(data['qty']), data['code']))
        cursor.execute("INSERT INTO WriteOffs (ProductCode, Qty, Reason, LogDate, LoggedBy) VALUES (%s, %s, %s, %s, %s)", (data['code'], float(data['qty']), data['reason'], datetime.now(), session.get('username')))
        conn.commit()
        return jsonify({"success": True, "message": f"Successfully wrote off {data['qty']}kg of {data['code']}."})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/delete-product', methods=['POST'])
def delete_product():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    if session.get('role', 'Staff').lower() not in ['admin', 'manager', 'owner']:
        return jsonify({"success": False, "message": "Access Denied: Managers or Admins only."}), 403
    code = request.get_json().get('code')
    if not code: return jsonify({"success": False, "message": "Missing Product Code"})
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = %s", (code,))
        prod_row = cursor.fetchone()
        if not prod_row: return jsonify({"success": False, "message": "Product not found."})
        prod_id = prod_row[0]
        cursor.execute("SELECT SaleDetailID FROM SalesDetails WHERE ProductID = %s", (prod_id,))
        if cursor.fetchone(): return jsonify({"success": False, "message": "Cannot delete item. It is linked to existing Sales records."})
        cursor.execute("SELECT PurchaseDetailID FROM PurchaseDetails WHERE ProductID = %s", (prod_id,))
        if cursor.fetchone(): return jsonify({"success": False, "message": "Cannot delete item. It is linked to existing Purchase records."})
        cursor.execute("DELETE FROM Products WHERE ProductID = %s", (prod_id,))
        conn.commit()
        return jsonify({"success": True, "message": "Item permanently deleted from catalog."})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


# --- LOGISTICS, MOVEMENT & CONTRACT ROUTES ---
@app.route('/api/trip-sheets', methods=['GET'])
def api_trip_sheets():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    branch = session.get('branch', 'NV')
    is_owner = (session.get('role', '').lower() == 'owner' or branch == 'ALL')
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if is_owner:
            cursor.execute("SELECT TOP 50 o.InvoiceNo, c.CustomerName, c.Address, o.PaymentStatus FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID WHERE o.FulfillmentMode = 'Delivery' ORDER BY o.SaleID DESC")
        else:
            cursor.execute("SELECT TOP 50 o.InvoiceNo, c.CustomerName, c.Address, o.PaymentStatus FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID WHERE o.FulfillmentMode = 'Delivery' AND o.Branch = %s ORDER BY o.SaleID DESC", (branch,))
            
        rows = cursor.fetchall()
        trips = []
        for i, r in enumerate(rows):
            addr = r[2].split(',')[0] if r[2] else "Customer Location"
            trips.append({
                "id": f"TRP-{r[0].replace('INV-', '')}", "driver": "Logistics Partner", "vehicle_no": "Assigned at Dispatch",
                "route": f"Warehouse ➔ {addr}", "order_count": 1, "status": "Delivered" if r[3] == 'Paid' else "In Transit"
            })
        if not trips: trips = [{"id": "TRP-STANDBY", "driver": "Fleet Team", "vehicle_no": "TBA", "route": "Awaiting Orders", "order_count": 0, "status": "Standby"}]
        return jsonify({"success": True, "trips": trips})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/api/get-movements', methods=['GET'])
def api_get_movements():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    branch = session.get('branch', 'NV')
    is_owner = (session.get('role', '').lower() == 'owner' or branch == 'ALL')
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        movements = []

        if is_owner:
            cursor.execute("SELECT TOP 50 o.PurchaseDate, p.ProductCode, d.Quantity, o.InvoiceNo FROM PurchaseDetails d INNER JOIN PurchaseOrders o ON d.PurchaseID = o.PurchaseID INNER JOIN Products p ON d.ProductID = p.ProductID ORDER BY o.PurchaseDate DESC")
        else:
            cursor.execute("SELECT TOP 50 o.PurchaseDate, p.ProductCode, d.Quantity, o.InvoiceNo FROM PurchaseDetails d INNER JOIN PurchaseOrders o ON d.PurchaseID = o.PurchaseID INNER JOIN Products p ON d.ProductID = p.ProductID WHERE o.Branch = %s ORDER BY o.PurchaseDate DESC", (branch,))
        for r in cursor.fetchall():
            movements.append({"date": r[0].strftime('%Y-%m-%d %H:%M') if r[0] else 'N/A', "code": r[1], "type": "IN", "qty": float(r[2]), "ref": r[3]})

        if is_owner:
            cursor.execute("SELECT TOP 50 o.SaleDate, p.ProductCode, d.Quantity, o.InvoiceNo FROM SalesDetails d INNER JOIN SalesOrders o ON d.SaleID = o.SaleID INNER JOIN Products p ON d.ProductID = p.ProductID ORDER BY o.SaleDate DESC")
        else:
            cursor.execute("SELECT TOP 50 o.SaleDate, p.ProductCode, d.Quantity, o.InvoiceNo FROM SalesDetails d INNER JOIN SalesOrders o ON d.SaleID = o.SaleID INNER JOIN Products p ON d.ProductID = p.ProductID WHERE o.Branch = %s ORDER BY o.SaleDate DESC", (branch,))
        for r in cursor.fetchall():
            movements.append({"date": r[0].strftime('%Y-%m-%d %H:%M') if r[0] else 'N/A', "code": r[1], "type": "OUT", "qty": float(r[2]), "ref": r[3]})

        movements.sort(key=lambda x: x["date"], reverse=True)
        return jsonify({"success": True, "movements": movements[:100]})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()


@app.route('/create-customer', methods=['POST'])
def create_customer():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    data = request.get_json()
    phone = data.get('phone', '').strip()
    full_addr = parse_full_address(data)
    area_id = data.get('area_id')
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT CustomerCode FROM Customers WHERE Phone = %s OR Address = %s", (phone, full_addr))
        if duplicate := cursor.fetchone():
            return jsonify({"success": False, "message": f"Profile duplication match caught under directory file code [{duplicate[0]}]. Address or Phone already exists."}), 400

        while True:
            new_code = str(random.randint(100000, 999999))
            cursor.execute("SELECT CustomerID FROM Customers WHERE CustomerCode = %s", (new_code,))
            if not cursor.fetchone(): break

        cursor.execute("INSERT INTO Customers (CustomerCode, CustomerName, Phone, Email, AreaID, Address) VALUES (%s, %s, %s, %s, %s, %s)", (new_code, data.get('name', ''), phone, data.get('email', ''), area_id, full_addr))
        conn.commit()
        return jsonify({"success": True, "customer_code": new_code, "message": "Profile indexed successfully!"})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/update-customer', methods=['POST'])
def api_update_customer():
    if not session.get('logged_in'): return jsonify({"success": False, "message": "Unauthorized"}), 401

    data = request.get_json()
    code = data.get('code')
    phone = data.get('phone')
    full_addr = parse_full_address(data)

    try: check_code = int(code)
    except (ValueError, TypeError): check_code = code

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT CustomerCode FROM Customers WHERE Phone = %s AND CustomerCode <> %s", (phone, check_code))
        if cursor.fetchone(): return jsonify({"success": False, "message": "A different profile with this phone number genuinely exists in the directory."})

        cursor.execute("UPDATE Customers SET CustomerName=%s, Phone=%s, Email=%s, Address=%s, AreaID=%s WHERE CustomerCode=%s", (data.get('name'), phone, data.get('email', ''), full_addr, data.get('area_id'), check_code))
        conn.commit()
        return jsonify({"success": True, "message": "Customer profile updated successfully!"})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/api/delete-customer', methods=['POST'])
def delete_customer():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    if session.get('role', 'Staff').lower() not in ['admin', 'owner']:
        return jsonify({"success": False, "message": "Clearance mismatch error. Requires Administrative permissions."}), 403

    code = request.get_json().get('code', '').strip()
    if not code: return jsonify({"success": False, "message": "Missing selection key."})
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT CustomerID FROM Customers WHERE CustomerCode = %s", (code,))
        cust_row = cursor.fetchone()
        if not cust_row: return jsonify({"success": False, "message": "Profile record target not found."})
        cust_id = cust_row[0]

        cursor.execute("SELECT SaleID FROM SalesOrders WHERE CustomerID = %s", (cust_id,))
        if cursor.fetchone(): return jsonify({"success": False, "message": "Erase block caught: This customer contains active ledger transaction history. To delete, remove their sales records first."}), 400

        cursor.execute("DELETE FROM Customers WHERE CustomerID = %s", (cust_id,))
        conn.commit()
        return jsonify({"success": True, "message": f"Customer profile row [{code}] deleted completely from storage blocks."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/create-seller', methods=['POST'])
def create_seller():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    data = request.get_json()
    full_addr = parse_full_address(data, prefix='seller_')
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT SupplierCode FROM Suppliers WHERE Phone = %s", (data.get('phone', ''),))
        if existing := cursor.fetchone(): return jsonify({"success": True, "seller_code": existing[0], "message": "Supplier already exists."})
        while True:
            new_code = str(random.randint(10000, 999999))
            cursor.execute("SELECT SupplierID FROM Suppliers WHERE SupplierCode = %s", (new_code,))
            if not cursor.fetchone(): break
        cursor.execute("INSERT INTO Suppliers (SupplierCode, SupplierName, Phone, Address) VALUES (%s, %s, %s, %s)", (new_code, data.get('name', ''), data.get('phone', ''), full_addr))
        conn.commit()
        return jsonify({"success": True, "seller_code": new_code})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/update-seller', methods=['POST'])
def update_seller():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    data = request.get_json()
    code = data.get('code', '').strip()
    full_addr = parse_full_address(data, prefix='seller_')
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE Suppliers SET SupplierName = %s, Phone = %s, Address = %s WHERE SupplierCode = %s", (data.get('name'), data.get('phone'), full_addr, code))
        conn.commit()
        return jsonify({"success": True, "message": "Supplier Updated Successfully!"})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/create-employee', methods=['POST'])
def create_employee():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    if session.get('role', 'Staff').lower() not in ['admin', 'manager', 'owner']: return jsonify({"success": False, "message": "Access Denied: Managers or Admins only."}), 403

    data = request.get_json()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        username, password = data.get('username', '').strip(), data.get('password', '').strip()
        if username and password:
            cursor.execute("SELECT id FROM SystemUsers WHERE username = %s", (username,))
            if cursor.fetchone(): return jsonify({"success": False, "message": f"Username '{username}' already exists. Choose a different one."}), 400
            cursor.execute("INSERT INTO SystemUsers (username, password_plain) VALUES (%s, %s)", (username, password))

        parts = data.get('name', 'Unknown Employee').split(' ', 1)
        assigned_branch = data.get('branch', 'NV')
        cursor.execute(
            "INSERT INTO Employees (FirstName, LastName, Position, Phone, Email, Username, Branch) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (parts[0], parts[1] if len(parts) > 1 else '', data.get('role', 'Staff'), data.get('phone', ''), '', username, assigned_branch))
        
        cursor.execute("SELECT LASTVAL()")
        emp_id = int(cursor.fetchone()[0])
        conn.commit()
        msg = "Employee added successfully!"
        if username: msg += " Login credentials generated."
        return jsonify({"success": True, "code": f"EMP-{emp_id}", "message": msg})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/update-employee', methods=['POST'])
def update_employee():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    if session.get('role', 'Staff').lower() not in ['admin', 'manager', 'owner']: return jsonify({"success": False, "message": "Access Denied: Managers or Admins only."}), 403

    data = request.get_json()
    code = data.get('code')
    if not code: return jsonify({"success": False, "message": "Missing ID"})

    emp_id = int(code.split('-')[1])
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        new_username, new_password = data.get('username', '').strip(), data.get('password', '').strip()
        parts = data.get('name', '').split(' ', 1)
        first_name, last_name = parts[0], parts[1] if len(parts) > 1 else ''
        assigned_branch = data.get('branch', 'NV')

        cursor.execute("SELECT Username FROM Employees WHERE EmployeeID = %s", (emp_id,))
        row = cursor.fetchone()
        old_username = row[0] if row else ""

        if new_username:
            if old_username:
                if old_username != new_username:
                    cursor.execute("SELECT id FROM SystemUsers WHERE username = %s", (new_username,))
                    if cursor.fetchone(): return jsonify({"success": False, "message": "New username already taken."}), 400
                if new_password:
                    cursor.execute("UPDATE SystemUsers SET username = %s, password_plain = %s WHERE username = %s", (new_username, new_password, old_username))
                else:
                    cursor.execute("UPDATE SystemUsers SET username = %s WHERE username = %s", (new_username, old_username))
            else:
                cursor.execute("SELECT id FROM SystemUsers WHERE username = %s", (new_username,))
                if cursor.fetchone(): return jsonify({"success": False, "message": "Username already taken."}), 400
                cursor.execute("INSERT INTO SystemUsers (username, password_plain) VALUES (%s, %s)", (new_username, new_password))
        else:
            if old_username: cursor.execute("DELETE FROM SystemUsers WHERE username = %s", (old_username,))

        cursor.execute(
            "UPDATE Employees SET FirstName = %s, LastName = %s, Position = %s, Phone = %s, Username = %s, Branch = %s WHERE EmployeeID = %s",
            (first_name, last_name, data.get('role', 'Staff'), data.get('phone', ''), new_username, assigned_branch, emp_id))
        conn.commit()
        return jsonify({"success": True, "message": "Employee profile updated successfully."})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/delete-employee', methods=['POST'])
def delete_employee():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    if session.get('role', 'Staff').lower() not in ['admin', 'manager', 'owner']: return jsonify({"success": False, "message": "Access Denied: Managers or Admins only."}), 403
    code = request.get_json().get('code')
    if not code: return jsonify({"success": False, "message": "Missing ID"})
    conn = None
    try:
        emp_id = int(code.split('-')[1])
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT Username FROM Employees WHERE EmployeeID = %s", (emp_id,))
        row = cursor.fetchone()
        if row and row[0]: cursor.execute("DELETE FROM SystemUsers WHERE username = %s", (row[0],))
        cursor.execute("DELETE FROM Employees WHERE EmployeeID = %s", (emp_id,))
        conn.commit()
        return jsonify({"success": True, "message": "Employee and Access Rights completely purged."})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/bulk-delete-orders', methods=['POST'])
def bulk_delete_orders():
    if not session.get('logged_in'): return jsonify({"success": False, "message": "Unauthorized"}), 401
    if session.get('role', 'Staff').lower() not in ['admin', 'owner']: return jsonify({"success": False, "message": "Clearance mismatch. Admin/Owner only."}), 403
    data = request.get_json()
    order_ids = data.get('order_ids', [])
    if not order_ids: return jsonify({"success": False, "message": "No orders selected."}), 400
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        for o_id in order_ids:
            try: order_id = int(o_id)
            except ValueError: continue
            cursor.execute("SELECT p.ProductCode, d.Quantity FROM SalesDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.SaleID = %s", (order_id,))
            for row in cursor.fetchall():
                cursor.execute("UPDATE Products SET StockQuantity = StockQuantity + %s WHERE ProductCode = %s", (float(row[1]), row[0]))
            cursor.execute("DELETE FROM OrderHistory WHERE SaleID = %s", (order_id,))
            cursor.execute("DELETE FROM SalesDetails WHERE SaleID = %s", (order_id,))
            cursor.execute("DELETE FROM Payments WHERE SaleID = %s", (order_id,))
            cursor.execute("DELETE FROM SalesOrders WHERE SaleID = %s", (order_id,))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

# --- REPORT ENGINE MODULES ---
@app.route('/api/daily-stock-summary')
def api_daily_stock_summary():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ProductCode, ProductName, StockQuantity FROM Products")
        summary_dict = {}
        for row in cursor.fetchall():
            summary_dict[row[0]] = {"code": row[0], "name": row[1], "sold": 0.0, "purchased": 0.0, "current": float(row[2] or 0)}
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        cursor.execute("SELECT p.ProductCode, d.Quantity FROM SalesDetails d INNER JOIN Products p ON d.ProductID = p.ProductID INNER JOIN SalesOrders o ON d.SaleID = o.SaleID WHERE o.SaleDate >= %s", (today_start,))
        for r in cursor.fetchall():
            if r[0] in summary_dict: summary_dict[r[0]]["sold"] += float(r[1] or 0)

        cursor.execute("SELECT p.ProductCode, d.Quantity FROM PurchaseDetails d INNER JOIN Products p ON d.ProductID = p.ProductID INNER JOIN PurchaseOrders o ON d.PurchaseID = o.PurchaseID WHERE o.PurchaseDate >= %s", (today_start,))
        for r in cursor.fetchall():
            if r[0] in summary_dict: summary_dict[r[0]]["purchased"] += float(r[1] or 0)

        results = []
        for k, v in summary_dict.items():
            starting_balance = v["current"] + v["sold"] - v["purchased"]
            results.append({**v, "starting": starting_balance})

        return jsonify({"success": True, "summary": results})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/api/account-statement')
def api_account_statement():
    customer_code = request.args.get('code', '').strip()
    if not customer_code: return jsonify({"success": False, "message": "Missing criteria."})
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT CustomerID, CustomerName FROM Customers WHERE CustomerCode = %s", (customer_code,))
        cust = cursor.fetchone()
        if not cust: return jsonify({"success": False, "message": "Account code invalid."})
        cust_id = cust[0]

        cursor.execute("SELECT SaleID, SaleDate, InvoiceNo, TotalAmount, PaymentMethod, PaymentStatus FROM SalesOrders WHERE CustomerID = %s ORDER BY SaleDate ASC", (cust_id,))
        statement = []
        running_dues = 0.0
        for row in cursor.fetchall():
            amt = float(row[3] or 0)
            is_pending = row[5] == 'Pending'
            if is_pending: running_dues += amt
            statement.append({"id": row[0], "date": row[1].strftime('%Y-%m-%d %H:%M') if row[1] else 'N/A', "invoice": row[2], "amount": amt, "method": row[4], "status": row[5], "balance": running_dues})
        return jsonify({"success": True, "customer_name": cust[1], "statement": statement, "total_outstanding": running_dues})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/api/product-statement')
def api_product_statement():
    product_code = request.args.get('code', '').strip()
    if not product_code: return jsonify({"success": False, "message": "Selection missing."})
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ProductID, ProductName, StockQuantity FROM Products WHERE ProductCode = %s", (product_code,))
        prod = cursor.fetchone()
        if not prod: return jsonify({"success": False, "message": "Invalid commodity selection."})
        prod_id, prod_name, current_stock = prod[0], prod[1], float(prod[2] or 0)

        cursor.execute("SELECT o.SaleDate, o.InvoiceNo, d.Quantity, d.UnitPrice FROM SalesDetails d INNER JOIN SalesOrders o ON d.SaleID = o.SaleID WHERE d.ProductID = %s ORDER BY o.SaleDate DESC", (prod_id,))
        sales_log = [{"date": r[0].strftime('%Y-%m-%d'), "ref": r[1], "qty": float(r[2]), "price": float(r[3]), "type": "OUT"} for r in cursor.fetchall()]

        cursor.execute("SELECT o.PurchaseDate, o.InvoiceNo, d.Quantity, d.UnitPrice FROM PurchaseDetails d INNER JOIN PurchaseOrders o ON d.PurchaseID = o.PurchaseID WHERE d.ProductID = %s ORDER BY o.PurchaseDate DESC", (prod_id,))
        purchases_log = [{"date": r[0].strftime('%Y-%m-%d'), "ref": r[1], "qty": float(r[2]), "price": float(r[3]), "type": "IN"} for r in cursor.fetchall()]

        combined_ledger = sales_log + purchases_log
        combined_ledger.sort(key=lambda x: x["date"], reverse=True)

        return jsonify({"success": True, "product_name": prod_name, "current_stock": current_stock, "ledger": combined_ledger[:50]})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/api/action-approval', methods=['POST'])
def api_action_approval():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    if session.get('role', 'Staff').lower() not in ['admin', 'manager', 'owner']: return jsonify({"success": False, "message": "Clearance denied."}), 403
    data = request.get_json()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE Approvals SET Status = %s WHERE ReqCode = %s", (data.get('action'), data.get('code')))
        conn.commit()
        return jsonify({"success": True, "message": f"Request {data.get('code')} has been {data.get('action')}."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

# =========================================================================================
# --- ADVANCED AI MASTER CONFIGURATION & FAILOVER SYSTEM ---
# =========================================================================================
API_KEY_POOL = [
    os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6IHABKGI3F1wM_GPAci1XGe0FXwTfDxSjww2l5y30sy2Q"),
    os.environ.get("GEMINI_API_KEY_2", "AQ.Ab8RN6JVYs-qkUtlZETCLKEycXolHeY2ljRgnkjhHzVeS6jmrQ"),
]
key_cycle = itertools.cycle(API_KEY_POOL)

def get_next_client():
    key = next(key_cycle)
    return genai.Client(api_key=key)

def generate_with_failover(contents, config, model='gemini-3.6-flash'):
    last_error = None
    for _ in range(len(API_KEY_POOL)):
        try:
            client = get_next_client()
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as e:
            last_error = e
            continue
    raise Exception(f"All API keys failed. Last error: {str(last_error)}")

@app.route('/api/ai-assistant', methods=['POST'])
def ai_assistant():
    if not session.get('logged_in'): return jsonify({"success": False, "message": "Unauthorized"}), 401
    user_message = request.get_json().get('message', '')
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ProductName, StockQuantity FROM Products")
        stock_data = ", ".join([f"{r[0]}: {r[1]}kg" for r in cursor.fetchall()])
        cursor.execute("SELECT c.CustomerName, o.TotalAmount FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID WHERE o.PaymentStatus = 'Pending'")
        dues_data = ", ".join([f"{r[0]} owes ₹{r[1]}" for r in cursor.fetchall()])
        system_prompt = f"You are the AI Co-Pilot for PZ Grain Enterprise Resource Planning. Answer questions accurately based ONLY on this live system data:\n\nINVENTORY: {stock_data if stock_data else 'No stock available.'}\nPENDING DUES: {dues_data if dues_data else 'No pending dues.'}\n\nKeep responses concise, clear, and business-focused. Do not invent data."
        response = generate_with_failover(contents=user_message, config={'system_instruction': system_prompt, 'temperature': 0.2}, model='gemini-3.6-flash')
        return jsonify({"success": True, "reply": response.text})
    except Exception as e:
        return jsonify({"success": False, "reply": f"AI Error: {str(e)}"}), 500
    finally:
        if conn: conn.close()

@app.route('/api/download-report-pdf', methods=['GET'])
def download_report_pdf():
    if not session.get('logged_in'): return redirect(url_for('login'))
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, 750, "PZ Grain Enterprise - Official Business Report")
    p.setFont("Helvetica", 10)
    p.drawString(50, 730, f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    p.drawString(50, 715, f"Generated by User: {session.get('username')}")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ProductName, StockQuantity FROM Products")
    products = cursor.fetchall()
    y = 670
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, "Current Warehouse Inventory Status:")
    y -= 25
    p.setFont("Helvetica", 10)
    for prod in products:
        p.drawString(70, y, f"- {prod[0]}: {prod[1]} kg available")
        y -= 20
        if y < 50:
            p.showPage()
            y = 750
    conn.close()
    p.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"PZ_Grain_Report_{datetime.now().strftime('%Y%m%d')}.pdf", mimetype='application/pdf')

@app.route('/api/ai-inventory-forecast', methods=['GET'])
def ai_inventory_forecast():
    if not session.get('logged_in'): return jsonify({"success": False, "message": "Unauthorized"}), 401
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ProductName, StockQuantity, SalesPrice, PurchasePrice FROM Products")
        products = cursor.fetchall()
        stock_summary = ", ".join([f"{p[0]}: {p[1]}kg (Sell: ₹{p[2]}/Buy: ₹{p[3]})" for p in products])
        system_prompt = "You are an expert commodity supply chain analyst for PZ Grain Enterprise."
        user_query = f"Review the following real-time inventory and pricing data from our warehouse:\n{stock_summary}\n\nProvide an executive procurement audit:\n1. Identify items at critical risk (under 1,000kg).\n2. Recommend which varieties need immediate reordering from suppliers.\n3. Suggest pricing or margin optimization strategies based on purchase vs. sales prices.\nKeep it professional, structured, and concise."
        response = generate_with_failover(contents=user_query, config={'system_instruction': system_prompt, 'temperature': 0.3})
        return jsonify({"success": True, "analysis": response.text})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/ai-collection-draft', methods=['POST'])
def ai_collection_draft():
    if not session.get('logged_in'): return jsonify({"success": False, "message": "Unauthorized"}), 401
    data = request.get_json()
    customer_name = data.get('customer_name', '').strip()
    if not customer_name: return jsonify({"success": False, "message": "Customer name required."}), 400
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT o.InvoiceNo, o.TotalAmount, o.SaleDate FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID WHERE c.CustomerName = %s AND o.PaymentStatus = 'Pending'", (customer_name,))
        dues = cursor.fetchall()
        if not dues: return jsonify({"success": True, "draft": f"No pending market dues found for {customer_name}!"})
        total_due = sum([float(r[1]) for r in dues])
        invoice_list = ", ".join([f"{r[0]} (₹{r[1]:,.2f})" for r in dues])
        user_query = f"Draft a polite, professional, and firm WhatsApp/Email collection message for our client: {customer_name}.\nTotal Outstanding Debt: ₹{total_due:,.2f}\nUnpaid Invoices: {invoice_list}\n\nMake it ready to copy-paste for our accounts team."
        response = generate_with_failover(contents=user_query, config={'temperature': 0.3})
        return jsonify({"success": True, "draft": response.text})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/ai-system-audit', methods=['GET'])
def ai_system_audit():
    if not session.get('logged_in'): return jsonify({"success": False, "message": "Unauthorized"}), 401
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ProductCode, ProductName, StockQuantity FROM Products WHERE StockQuantity < 0")
        negative_stock = cursor.fetchall()
        cursor.execute("SELECT COUNT(*), SUM(TotalAmount) FROM SalesOrders WHERE PaymentStatus = 'Pending'")
        pending_summary = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM Customers")
        total_customers = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM Products")
        total_products = cursor.fetchone()[0]
        audit_data = f"- Negative Stock Anomalies: {len(negative_stock)} items found ({', '.join([p[1] for p in negative_stock]) if negative_stock else 'None'})\n- Total Unpaid Orders: {pending_summary[0] if pending_summary else 0} orders\n- Total Outstanding Debt Exposure: ₹{pending_summary[1] if pending_summary and pending_summary[1] else 0:,.2f}\n- Total Client Profiles Indexed: {total_customers}\n- Total Active Products in Catalog: {total_products}\n"
        system_prompt = "You are the Senior Enterprise Software Architect and Database Integrity AI for PZ Grain ERP."
        user_query = f"Perform an automated system health and database integrity scan based on these live runtime metrics:\n{audit_data}\n\nProvide an automated performance optimization report:\n1. Enterprise System Health Score (out of 100) with a brief justification.\n2. Detected database bottlenecks or transactional risks.\n3. Specific code patches or database index optimizations to improve execution speed and reliability.\nKeep it structured, technical, and concise."
        response = generate_with_failover(contents=user_query, config={'system_instruction': system_prompt, 'temperature': 0.2})
        return jsonify({"success": True, "audit_report": response.text})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


if __name__ == '__main__':
    from waitress import serve
    print("🚀 Enterprise Server running on Port 5000 (Optimized for 20+ Concurrent Terminals)")
    serve(app, host='0.0.0.0', port=5000, threads=50, connection_limit=100)
