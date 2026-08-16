import os
import json
import random
import traceback
import pyodbc
import shutil
import threading
import time
from collections import defaultdict
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'pz-grain-secret-key-2026')

DB_PATH = r"C:\Users\modik\OneDrive\Desktop\PZ\PZ_Grain_DB.accdb"
CONN_STRING = f"Driver={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={DB_PATH};"


def get_db_connection():
    return pyodbc.connect(CONN_STRING)


# --- AUTOMATED BACKGROUND BACKUP ENGINE ---
def run_daily_backups():
    """Silently copies the MS Access database to a backup folder every night at 2:00 AM."""
    backup_dir = r"C:\Users\modik\OneDrive\Desktop\PZ\Backups"
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
    print("🛡️ Automated Backup Engine Initialized. Monitoring for 2:00 AM schedule.")
    while True:
        try:
            now = datetime.now()
            if now.hour == 2 and now.minute == 0:
                backup_filename = f"PZ_DB_Backup_{now.strftime('%Y%m%d_%H%M%S')}.accdb"
                backup_path = os.path.join(backup_dir, backup_filename)
                shutil.copy2(DB_PATH, backup_path)
                print(f"✅ Automated Backup Successful: {backup_filename}")
                time.sleep(61)
        except Exception as e:
            print(f"❌ Backup Engine Error: {e}")
        time.sleep(30)


# --- AUTO-HEALING DATABASE ENGINE ---
def init_db_updates():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT TOP 1 HistoryID FROM OrderHistory")
        except:
            cursor.execute(
                "CREATE TABLE OrderHistory (HistoryID COUNTER PRIMARY KEY, SaleID INTEGER, ModificationDate DATETIME, PreviousTotal CURRENCY, PreviousItems MEMO)"); conn.commit()

        try:
            cursor.execute("SELECT TOP 1 FulfillmentMode FROM SalesOrders")
        except:
            cursor.execute(
                "ALTER TABLE SalesOrders ADD COLUMN FulfillmentMode VARCHAR(50)"); conn.commit(); cursor.execute(
                "UPDATE SalesOrders SET FulfillmentMode = 'Takeaway' WHERE FulfillmentMode IS NULL"); conn.commit()

        try:
            cursor.execute("SELECT TOP 1 Username FROM Employees")
        except:
            cursor.execute("ALTER TABLE Employees ADD COLUMN Username VARCHAR(50)"); conn.commit()

        try:
            cursor.execute("SELECT TOP 1 Phone FROM Employees")
        except:
            cursor.execute("ALTER TABLE Employees ADD COLUMN Phone VARCHAR(50)"); conn.commit()

        try:
            cursor.execute("SELECT TOP 1 Email FROM Employees")
        except:
            cursor.execute("ALTER TABLE Employees ADD COLUMN Email VARCHAR(100)"); conn.commit()

        try:
            cursor.execute("SELECT TOP 1 id FROM SystemUsers")
        except Exception:
            cursor.execute(
                "CREATE TABLE SystemUsers (id COUNTER PRIMARY KEY, username VARCHAR(50), password_plain VARCHAR(50))")
            conn.commit()

        cursor.execute("SELECT id FROM SystemUsers WHERE username = 'admin'")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO SystemUsers (username, password_plain) VALUES ('admin', 'admin')")
            conn.commit()
            print("👑 Auto-Heal: Generated default Admin account (admin/admin).")
            try:
                cursor.execute(
                    "INSERT INTO Employees (FirstName, LastName, Position, Phone, Email, Username) VALUES ('System', 'Admin', 'Admin', 'N/A', 'admin@pz.com', 'admin')")
                conn.commit()
            except Exception:
                pass

        try:
            cursor.execute("SELECT TOP 1 PurchasePrice FROM Products")
        except:
            cursor.execute("ALTER TABLE Products ADD COLUMN PurchasePrice FLOAT");
            cursor.execute("ALTER TABLE Products ADD COLUMN SalesPrice FLOAT");
            conn.commit()
            cursor.execute("UPDATE Products SET SalesPrice = UnitPrice, PurchasePrice = UnitPrice");
            conn.commit()

        try:
            cursor.execute("SELECT TOP 1 TaxRate FROM Products")
        except:
            cursor.execute("ALTER TABLE Products ADD COLUMN TaxRate FLOAT");
            conn.commit()
            cursor.execute("UPDATE Products SET TaxRate = 0");
            conn.commit()

        try:
            cursor.execute("SELECT TOP 1 TaxAmount FROM SalesOrders")
        except:
            cursor.execute("ALTER TABLE SalesOrders ADD COLUMN TaxAmount FLOAT"); conn.commit()

        try:
            cursor.execute("SELECT TOP 1 ExpenseID FROM Expenses")
        except:
            cursor.execute(
                "CREATE TABLE Expenses (ExpenseID COUNTER PRIMARY KEY, ExpDate DATETIME, Category VARCHAR(100), Amount FLOAT, Notes TEXT)")
            conn.commit()

        try:
            cursor.execute("SELECT TOP 1 LogID FROM WriteOffs")
        except:
            cursor.execute(
                "CREATE TABLE WriteOffs (LogID COUNTER PRIMARY KEY, ProductCode VARCHAR(50), Qty FLOAT, Reason VARCHAR(100), LogDate DATETIME, LoggedBy VARCHAR(50))")
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
                "SELECT u.id, u.password_plain, e.Position FROM SystemUsers u LEFT JOIN Employees e ON u.username = e.Username WHERE u.username = ?",
                (username,))
            user_row = cursor.fetchone()
            if user_row and user_row[1] == password:
                session['logged_in'] = True
                session['username'] = username
                session['role'] = user_row[2].strip() if len(user_row) > 2 and user_row[2] else 'Staff'
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
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT ProductCode, ProductName, SalesPrice, PurchasePrice, UnitPrice, Unit, StockQuantity, TaxRate FROM Products ORDER BY ProductCode ASC")
        grains = []
        for r in cursor.fetchall():
            s_price = float(r[2] if r[2] is not None else (r[4] if r[4] is not None else 0))
            p_price = float(r[3] if r[3] is not None else (r[4] if r[4] is not None else 0))
            grains.append({
                "code": r[0], "name": r[1], "sales_price": s_price, "purchase_price": p_price, "unit": r[5],
                "stock_kg": float(r[6] if r[6] is not None else 0), "price_per_kg": s_price,
                "tax_rate": float(r[7] if r[7] is not None else 0)
            })

        orders = []
        cursor.execute(
            "SELECT o.SaleID, c.CustomerName, c.Phone, c.Address, o.TotalAmount, c.CustomerCode, o.PaymentMethod, o.InvoiceNo, c.Email, o.FulfillmentMode, o.TaxAmount FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID ORDER BY o.SaleID DESC")
        for row in cursor.fetchall():
            sale_id = row[0]
            cursor.execute(
                "SELECT p.ProductCode, p.ProductName, d.Quantity, d.UnitPrice FROM SalesDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.SaleID = ? ORDER BY d.SaleDetailID ASC",
                (sale_id,))
            items = [{"code": d[0], "name": d[1], "qty": float(d[2]), "price": float(d[3])} for d in cursor.fetchall()]

            cursor.execute(
                "SELECT ModificationDate, PreviousTotal, PreviousItems FROM OrderHistory WHERE SaleID = ? ORDER BY ModificationDate DESC",
                (sale_id,))
            history = []
            for h in cursor.fetchall():
                try:
                    parsed_items = json.loads(h[2]) if h[2] else []
                except:
                    parsed_items = []
                history.append({"date": h[0].strftime('%Y-%m-%d %I:%M %p') if h[0] else 'N/A',
                                "old_total": float(h[1] if h[1] is not None else 0.0), "old_items": parsed_items})

            orders.append({
                "id": sale_id, "customer_name": row[1], "customer_phone": row[2] or "",
                "customer_address": row[3] or "",
                "items_raw": json.dumps(items), "total_amount": float(row[4] if row[4] is not None else 0.0),
                "customer_code": row[5], "payment_method": row[6], "invoice_no": row[7] or "",
                "customer_email": row[8] or "",
                "fulfillment_mode": row[9] or "Takeaway",
                "tax_amount": float(row[10] if len(row) > 10 and row[10] else 0),
                "history": json.dumps(history)
            })

        purchases = []
        cursor.execute(
            "SELECT o.PurchaseID, s.SupplierName, s.Phone, s.Address, o.TotalAmount, o.PurchaseDate, o.InvoiceNo FROM PurchaseOrders o INNER JOIN Suppliers s ON o.SupplierID = s.SupplierID ORDER BY o.PurchaseID DESC")
        for row in cursor.fetchall():
            pur_id = row[0]
            cursor.execute(
                "SELECT p.ProductCode, p.ProductName, d.Quantity, d.UnitPrice FROM PurchaseDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.PurchaseID = ? ORDER BY d.PurchaseDetailID ASC",
                (pur_id,))
            items = [{"code": d[0], "name": d[1], "qty": float(d[2]), "price": float(d[3])} for d in cursor.fetchall()]
            purchases.append(
                {"id": pur_id, "seller_name": row[1], "seller_phone": row[2] or "", "seller_address": row[3] or "",
                 "items_raw": json.dumps(items), "total_cost": float(row[4] if row[4] else 0),
                 "date": row[5].strftime('%d-%b-%Y') if row[5] else 'N/A', "invoice_no": row[6] or ""})

        cursor.execute(
            "SELECT CustomerCode, CustomerName, Phone, Email, Address FROM Customers ORDER BY CustomerName ASC")
        customers = [{"code": r[0], "name": r[1], "phone": r[2] or "", "email": r[3] or "", "address": r[4] or ""} for r
                     in cursor.fetchall()]

        cursor.execute("SELECT SupplierCode, SupplierName, Phone, Address FROM Suppliers ORDER BY SupplierName ASC")
        suppliers = [{"code": r[0], "name": r[1], "phone": r[2] or "", "address": r[3] or ""} for r in
                     cursor.fetchall()]

        cursor.execute(
            "SELECT EmployeeID, FirstName, LastName, Position, Username, Phone FROM Employees ORDER BY EmployeeID ASC")
        employees = [{"code": f"EMP-{r[0]}", "name": f"{r[1]} {r[2]}".strip(), "role": r[3], "username": r[4] or "",
                      "phone": r[5] or ""} for r in cursor.fetchall()]

        cursor.execute("SELECT PaymentID, SaleID, PaymentDate, Amount, Method FROM Payments ORDER BY PaymentID DESC")
        payments = [{"ref": f"ORD-{r[1]}", "flow": "IN", "amount": float(r[3] if r[3] else 0), "method": r[4] or "Cash",
                     "date": r[2].strftime('%Y-%m-%d %H:%M') if r[2] else 'N/A'} for r in cursor.fetchall()]

        cursor.execute("SELECT ExpDate, Category, Amount, Notes FROM Expenses ORDER BY ExpDate DESC")
        expenses = [{"date": r[0].strftime('%Y-%m-%d'), "category": r[1], "amount": float(r[2] if r[2] else 0),
                     "notes": r[3] or ""} for r in cursor.fetchall()]

        cursor.execute(
            "SELECT c.CustomerName, c.Phone, o.SaleDate, o.TotalAmount FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID WHERE o.PaymentStatus = 'Pending'")
        ar_dict = defaultdict(lambda: {"0_30": 0, "31_60": 0, "61_90": 0, "90_plus": 0, "total": 0, "phone": ""})
        for r in cursor.fetchall():
            name, phone, sdate, amt = r[0], r[1], r[2], float(r[3] or 0)
            if isinstance(sdate, str):
                try:
                    sdate = datetime.strptime(sdate.split('.')[0], '%Y-%m-%d %H:%M:%S')
                except:
                    sdate = datetime.now()
            days = (datetime.now() - sdate).days if isinstance(sdate, datetime) else 0
            ar_dict[name]["phone"] = phone or "N/A"
            ar_dict[name]["total"] += amt
            if days <= 30:
                ar_dict[name]["0_30"] += amt
            elif days <= 60:
                ar_dict[name]["31_60"] += amt
            elif days <= 90:
                ar_dict[name]["61_90"] += amt
            else:
                ar_dict[name]["90_plus"] += amt
        ar_aging = [{"name": k, **v} for k, v in ar_dict.items()]
        ar_aging.sort(key=lambda x: x["total"], reverse=True)

        cursor.execute(
            "SELECT SUM(sd.Quantity * p.PurchasePrice) FROM SalesDetails sd INNER JOIN Products p ON sd.ProductID = p.ProductID")
        cogs_val = cursor.fetchone()
        total_cogs = float(cogs_val[0] if cogs_val and cogs_val[0] else 0.0)

        return render_template('pos.html', grains=grains, orders=orders, purchases=purchases, customers=customers,
                               suppliers=suppliers, employees=employees, payments=payments, movements=[],
                               expenses=expenses, ar_aging=ar_aging, total_cogs=total_cogs)
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
    invoice_no = f"INV-{int(now.timestamp())}"
    conn = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        requested_volumes = defaultdict(float)
        for item in data['items']: requested_volumes[item['code']] += float(item['qty'])

        for code, total_qty in requested_volumes.items():
            cursor.execute("SELECT StockQuantity FROM Products WHERE ProductCode = ?", (code,))
            row = cursor.fetchone()
            if not row or float(row[0]) < total_qty:
                return jsonify({"success": False, "message": f"Insufficient stock for variety code [{code}]"}), 400

        full_addr = f"{data.get('street', '')}, {data.get('city', '')}, {data.get('state', '')} {data.get('zipcode', '')}, {data.get('country', '')}".strip(
            ', ')
        phone = data.get('customer_phone', '').strip()
        email = data.get('customer_email', '').strip()
        req_code = data.get('customer_code', '').strip()
        fulfillment_mode = data.get('fulfillment_mode', 'Takeaway')
        tax_amount = data.get('tax_amount', 0.0)

        cust_id, final_code = None, req_code

        if req_code and req_code.lower() != "new account":
            cursor.execute("SELECT CustomerID FROM Customers WHERE CustomerCode = ?", (req_code,))
            cust_row = cursor.fetchone()
            if cust_row:
                cust_id = int(cust_row[0])
                cursor.execute(
                    "UPDATE Customers SET Address = ?, Email = ?, CustomerName = ?, Phone = ? WHERE CustomerID = ?",
                    (full_addr, email, data.get('customer_name', ''), phone, cust_id))

        if not cust_id:
            cursor.execute("SELECT CustomerID, CustomerCode FROM Customers WHERE Phone = ?", (phone,))
            cust_row = cursor.fetchone()
            if cust_row:
                cust_id = int(cust_row[0])
                final_code = cust_row[1]
                cursor.execute("UPDATE Customers SET Address = ?, Email = ?, CustomerName = ? WHERE CustomerID = ?",
                               (full_addr, email, data.get('customer_name', ''), cust_id))
            else:
                while True:
                    final_code = str(random.randint(100000, 999999))
                    cursor.execute("SELECT CustomerID FROM Customers WHERE CustomerCode = ?", (final_code,))
                    if not cursor.fetchone(): break
                cursor.execute(
                    "INSERT INTO Customers (CustomerCode, CustomerName, Phone, Email, Address) VALUES (?, ?, ?, ?, ?)",
                    (final_code, data.get('customer_name', ''), phone, email, full_addr))
                cursor.execute("SELECT @@IDENTITY")
                cust_id = int(cursor.fetchone()[0])

        method = data.get('payment_method', 'Cash')
        split_payments = data.get('split_payments')
        status = 'Pending' if method in ['Pay Later', 'Cash on Delivery'] else 'Paid'

        cursor.execute(
            "INSERT INTO SalesOrders (CustomerID, SaleDate, InvoiceNo, TotalAmount, TaxAmount, PaymentMethod, PaymentStatus, FulfillmentMode) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cust_id, now, invoice_no, data['total'], tax_amount, method, status, fulfillment_mode))
        cursor.execute("SELECT @@IDENTITY")
        sale_id = int(cursor.fetchone()[0])

        for item in data['items']:
            cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = ?", (item['code'],))
            prod_row = cursor.fetchone()
            if not prod_row:
                conn.rollback()
                return jsonify({"success": False, "message": f"Invalid Product Code: {item['code']}"}), 400
            prod_id = int(prod_row[0])
            cursor.execute("UPDATE Products SET StockQuantity = StockQuantity - ? WHERE ProductID = ?",
                           (float(item['qty']), prod_id))
            cursor.execute(
                "INSERT INTO SalesDetails (SaleID, ProductID, Quantity, UnitPrice, Total) VALUES (?, ?, ?, ?, ?)",
                (sale_id, prod_id, float(item['qty']), float(item['price']), float(item['qty']) * float(item['price'])))

        if status == 'Paid':
            if split_payments:
                for sp in split_payments: cursor.execute(
                    "INSERT INTO Payments (SaleID, PaymentDate, Amount, Method) VALUES (?, ?, ?, ?)",
                    (sale_id, now, sp['amount'], sp['method']))
            else:
                cursor.execute("INSERT INTO Payments (SaleID, PaymentDate, Amount, Method) VALUES (?, ?, ?, ?)",
                               (sale_id, now, data['total'], method))

        conn.commit()
        return jsonify({"success": True, "order_id": sale_id, "customer_code": final_code, "invoice_no": invoice_no})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
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
        cursor.execute("SELECT TotalAmount FROM SalesOrders WHERE SaleID = ?", (order_id,))
        old_total_fetch = cursor.fetchone()
        old_total = float(old_total_fetch[0] if old_total_fetch and old_total_fetch[0] is not None else 0.0)

        cursor.execute(
            "SELECT p.ProductCode, p.ProductName, d.Quantity, d.UnitPrice FROM SalesDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.SaleID = ?",
            (order_id,))
        old_items = [{"code": r[0], "name": r[1], "qty": float(r[2]), "price": float(r[3])} for r in cursor.fetchall()]

        cursor.execute(
            "INSERT INTO OrderHistory (SaleID, ModificationDate, PreviousTotal, PreviousItems) VALUES (?, ?, ?, ?)",
            (order_id, datetime.now(), old_total, json.dumps(old_items)))

        for item in old_items: cursor.execute(
            "UPDATE Products SET StockQuantity = StockQuantity + ? WHERE ProductCode = ?", (item['qty'], item['code']))

        requested_volumes = defaultdict(float)
        for item in data['items']: requested_volumes[item['code']] += float(item['qty'])
        for code, total_qty in requested_volumes.items():
            cursor.execute("SELECT StockQuantity FROM Products WHERE ProductCode = ?", (code,))
            row = cursor.fetchone()
            if not row or float(row[0]) < total_qty:
                conn.rollback()
                return jsonify(
                    {"success": False, "message": f"Modification rejected: Insufficient stock for [{code}]"}), 400

        cursor.execute("DELETE FROM SalesDetails WHERE SaleID = ?", (order_id,))
        for item in data['items']:
            cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = ?", (item['code'],))
            prod_row = cursor.fetchone()
            if not prod_row:
                conn.rollback()
                return jsonify({"success": False, "message": f"Invalid Product Code: {item['code']}"}), 400
            prod_id = int(prod_row[0])
            cursor.execute("UPDATE Products SET StockQuantity = StockQuantity - ? WHERE ProductID = ?",
                           (float(item['qty']), prod_id))
            cursor.execute(
                "INSERT INTO SalesDetails (SaleID, ProductID, Quantity, UnitPrice, Total) VALUES (?, ?, ?, ?, ?)",
                (order_id, prod_id, float(item['qty']), float(item['price']),
                 float(item['qty']) * float(item['price'])))

        cursor.execute("SELECT CustomerID FROM SalesOrders WHERE SaleID = ?", (order_id,))
        cust_id = int(cursor.fetchone()[0])
        new_method = data.get('payment_method', 'Cash')
        split_payments = data.get('split_payments')
        fulfillment_mode = data.get('fulfillment_mode', 'Takeaway')
        new_status = 'Pending' if new_method in ['Pay Later', 'Cash on Delivery'] else 'Paid'

        # FIX APPLIED: Update the actual Address and TaxAmount dynamically in the DB
        cursor.execute("UPDATE Customers SET CustomerName = ?, Phone = ?, Address = ? WHERE CustomerID = ?",
                       (data.get('customer_name'), data.get('customer_phone'), data.get('customer_address'), cust_id))

        cursor.execute(
            "UPDATE SalesOrders SET TotalAmount = ?, TaxAmount = ?, PaymentMethod = ?, PaymentStatus = ?, FulfillmentMode = ? WHERE SaleID = ?",
            (data['total'], data.get('tax_amount', 0.0), new_method, new_status, fulfillment_mode, order_id))

        cursor.execute("DELETE FROM Payments WHERE SaleID = ?", (order_id,))
        if new_status == 'Paid':
            if split_payments:
                for sp in split_payments: cursor.execute(
                    "INSERT INTO Payments (SaleID, PaymentDate, Amount, Method) VALUES (?, ?, ?, ?)",
                    (order_id, datetime.now(), sp['amount'], sp['method']))
            else:
                cursor.execute("INSERT INTO Payments (SaleID, PaymentDate, Amount, Method) VALUES (?, ?, ?, ?)",
                               (order_id, datetime.now(), data['total'], new_method))

        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/submit-purchase', methods=['POST'])
def submit_purchase():
    if not session.get('logged_in'): return jsonify({"success": False, "message": "Unauthorized"}), 401
    data = request.get_json()
    now = datetime.now()
    invoice_no = f"PUR-{int(now.timestamp())}"
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        phone, req_code = data.get('seller_phone', '').strip(), data.get('seller_code', '').strip()
        sup_id = None

        if req_code and req_code.lower() not in ["", "auto-generated", "new account"]:
            cursor.execute("SELECT SupplierID FROM Suppliers WHERE SupplierCode = ?", (req_code,))
            sup_row = cursor.fetchone()
            if sup_row:
                sup_id = int(sup_row[0])
                cursor.execute("UPDATE Suppliers SET SupplierName = ?, Phone = ?, Address = ? WHERE SupplierID = ?",
                               (data.get('seller_name', ''), phone, data.get('seller_address', ''), sup_id))

        if not sup_id:
            cursor.execute("SELECT SupplierID FROM Suppliers WHERE Phone = ?", (phone,))
            sup_row = cursor.fetchone()
            if sup_row:
                sup_id = int(sup_row[0])
                cursor.execute("UPDATE Suppliers SET SupplierName = ?, Address = ? WHERE SupplierID = ?",
                               (data.get('seller_name', ''), data.get('seller_address', ''), sup_id))
            else:
                while True:
                    sup_code = str(random.randint(10000, 999999))
                    cursor.execute("SELECT SupplierID FROM Suppliers WHERE SupplierCode = ?", (sup_code,))
                    if not cursor.fetchone(): break
                cursor.execute("INSERT INTO Suppliers (SupplierCode, SupplierName, Phone, Address) VALUES (?, ?, ?, ?)",
                               (sup_code, data.get('seller_name', ''), phone, data.get('seller_address', '')))
                cursor.execute("SELECT @@IDENTITY")
                sup_id = int(cursor.fetchone()[0])

        cursor.execute(
            "INSERT INTO PurchaseOrders (SupplierID, PurchaseDate, InvoiceNo, TotalAmount, PaymentStatus) VALUES (?, ?, ?, ?, ?)",
            (sup_id, now, invoice_no, data['total_cost'], 'Paid'))
        cursor.execute("SELECT @@IDENTITY")
        pur_id = int(cursor.fetchone()[0])

        for item in data['items']:
            cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = ?", (item['code'],))
            prod_row = cursor.fetchone()
            if not prod_row:
                conn.rollback()
                return jsonify({"success": False, "message": f"Invalid Product Code: {item['code']}"}), 400
            prod_id = int(prod_row[0])
            cursor.execute("UPDATE Products SET StockQuantity = StockQuantity + ? WHERE ProductID = ?",
                           (float(item['qty']), prod_id))
            cursor.execute(
                "INSERT INTO PurchaseDetails (PurchaseID, ProductID, Quantity, UnitPrice, Total) VALUES (?, ?, ?, ?, ?)",
                (pur_id, prod_id, float(item['qty']), float(item['price']), float(item['qty']) * float(item['price'])))

        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
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
        cursor.execute(
            "SELECT p.ProductCode, p.ProductName, d.Quantity, d.UnitPrice FROM PurchaseDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.PurchaseID = ?",
            (pur_id,))
        old_items = [{"code": r[0], "qty": float(r[2])} for r in cursor.fetchall()]
        for item in old_items: cursor.execute(
            "UPDATE Products SET StockQuantity = StockQuantity - ? WHERE ProductCode = ?", (item['qty'], item['code']))
        cursor.execute("DELETE FROM PurchaseDetails WHERE PurchaseID = ?", (pur_id,))
        for item in data['items']:
            cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = ?", (item['code'],))
            prod_row = cursor.fetchone()
            if not prod_row:
                conn.rollback()
                return jsonify({"success": False, "message": f"Invalid Product Code: {item['code']}"}), 400
            prod_id = int(prod_row[0])
            cursor.execute("UPDATE Products SET StockQuantity = StockQuantity + ? WHERE ProductID = ?",
                           (float(item['qty']), prod_id))
            item_total = float(item['qty']) * float(item['price'])
            cursor.execute(
                "INSERT INTO PurchaseDetails (PurchaseID, ProductID, Quantity, UnitPrice, Total) VALUES (?, ?, ?, ?, ?)",
                (pur_id, prod_id, float(item['qty']), float(item['price']), item_total))
        cursor.execute("SELECT SupplierID FROM PurchaseOrders WHERE PurchaseID = ?", (pur_id,))
        sup_id = int(cursor.fetchone()[0])
        cursor.execute("UPDATE Suppliers SET SupplierName = ?, Phone = ?, Address = ? WHERE SupplierID = ?",
                       (data.get('seller_name'), data.get('seller_phone'), data.get('seller_address'), sup_id))
        cursor.execute("UPDATE PurchaseOrders SET TotalAmount = ? WHERE PurchaseID = ?", (data['total'], pur_id))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
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
                cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = ?", (code,))
                if cursor.fetchone(): return jsonify(
                    {"success": False, "message": "Target Product Code already exists!"})
            if not is_management:
                cursor.execute("SELECT PurchasePrice FROM Products WHERE ProductCode = ?", (orig_code,))
                existing_pur = cursor.fetchone()
                pur_price = float(existing_pur[0]) if existing_pur and existing_pur[0] is not None else 0.0
            cursor.execute(
                "UPDATE Products SET ProductCode=?, ProductName=?, PurchasePrice=?, SalesPrice=?, TaxRate=? WHERE ProductCode=?",
                (code, name, pur_price, sal_price, tax_rate, orig_code))
            msg = "Item updated successfully!"
        else:
            cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = ?", (code,))
            if cursor.fetchone(): return jsonify({"success": False, "message": "Product Code already exists!"})
            if not is_management: pur_price = 0.0
            cursor.execute(
                "INSERT INTO Products (ProductCode, ProductName, PurchasePrice, SalesPrice, TaxRate, StockQuantity, Unit) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (code, name, pur_price, sal_price, tax_rate, 0, 'kg'))
            msg = "New item added successfully!"
        conn.commit()
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/api/add-expense', methods=['POST'])
def add_expense():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    user_role = session.get('role', 'Staff').lower()
    if user_role not in ['admin', 'manager', 'owner']:
        return jsonify({"success": False, "message": "Access Denied."}), 403

    data = request.get_json()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO Expenses (ExpDate, Category, Amount, Notes) VALUES (?, ?, ?, ?)",
                       (datetime.now(), data.get('category'), float(data.get('amount', 0)), data.get('notes', '')))
        conn.commit()
        return jsonify({"success": True, "message": "Expense Logged Successfully."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/api/write-off-stock', methods=['POST'])
def write_off_stock():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    user_role = session.get('role', 'Staff').lower()
    if user_role not in ['admin', 'manager', 'owner']:
        return jsonify({"success": False, "message": "Access Denied."}), 403

    data = request.get_json()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Verify Stock
        cursor.execute("SELECT StockQuantity FROM Products WHERE ProductCode = ?", (data['code'],))
        row = cursor.fetchone()
        if not row or float(row[0]) < float(data['qty']):
            return jsonify({"success": False, "message": "Cannot write-off more stock than is available."}), 400

        cursor.execute("UPDATE Products SET StockQuantity = StockQuantity - ? WHERE ProductCode = ?",
                       (float(data['qty']), data['code']))
        cursor.execute("INSERT INTO WriteOffs (ProductCode, Qty, Reason, LogDate, LoggedBy) VALUES (?, ?, ?, ?, ?)",
                       (data['code'], float(data['qty']), data['reason'], datetime.now(), session.get('username')))
        conn.commit()
        return jsonify({"success": True, "message": f"Successfully wrote off {data['qty']}kg of {data['code']}."})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/api/delete-product', methods=['POST'])
def delete_product():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    user_role = session.get('role', 'Staff').lower()
    if user_role not in ['admin', 'manager', 'owner']:
        return jsonify({"success": False, "message": "Access Denied: Managers or Admins only."}), 403
    code = request.get_json().get('code')
    if not code: return jsonify({"success": False, "message": "Missing Product Code"})
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ProductID FROM Products WHERE ProductCode = ?", (code,))
        prod_row = cursor.fetchone()
        if not prod_row: return jsonify({"success": False, "message": "Product not found."})
        prod_id = prod_row[0]
        cursor.execute("SELECT SaleDetailID FROM SalesDetails WHERE ProductID = ?", (prod_id,))
        if cursor.fetchone(): return jsonify(
            {"success": False, "message": "Cannot delete item. It is linked to existing Sales records."})
        cursor.execute("SELECT PurchaseDetailID FROM PurchaseDetails WHERE ProductID = ?", (prod_id,))
        if cursor.fetchone(): return jsonify(
            {"success": False, "message": "Cannot delete item. It is linked to existing Purchase records."})
        cursor.execute("DELETE FROM Products WHERE ProductID = ?", (prod_id,))
        conn.commit()
        return jsonify({"success": True, "message": "Item permanently deleted from catalog."})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


# --- UTILITY ROUTES (Search, Users, Lookup) ---
@app.route('/api/trip-sheets', methods=['GET'])
def api_trip_sheets():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT o.InvoiceNo, c.CustomerName, c.Phone, c.Address, o.TotalAmount, o.PaymentMethod, o.FulfillmentMode FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID WHERE o.PaymentStatus = 'Pending' AND o.FulfillmentMode = 'Delivery'")
        deliveries = [
            {"invoice": r[0], "name": r[1], "phone": r[2], "address": r[3], "total": float(r[4] if r[4] else 0),
             "method": r[5]} for r in cursor.fetchall()]
        return jsonify({"success": True, "deliveries": deliveries})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()


@app.route('/api/customer-debt', methods=['GET'])
def api_customer_debt():
    code = request.args.get('code')
    if not code: return jsonify({"success": False, "debt": 0})
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT SUM(o.TotalAmount) FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID WHERE c.CustomerCode = ? AND o.PaymentStatus = 'Pending'",
            (code,))
        row = cursor.fetchone()
        return jsonify({"success": True, "debt": float(row[0] if row and row[0] else 0.0)})
    except Exception:
        return jsonify({"success": False, "debt": 0})
    finally:
        if conn: conn.close()


@app.route('/api/pending-sales', methods=['GET'])
def api_pending_sales():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT o.SaleID, o.InvoiceNo, c.CustomerName, c.Phone, o.TotalAmount, o.SaleDate, o.PaymentMethod, c.CustomerCode, o.FulfillmentMode FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID WHERE o.PaymentStatus = 'Pending' ORDER BY o.SaleDate ASC")
        orders = []
        for row in cursor.fetchall():
            sale_id = row[0]
            cursor.execute(
                "SELECT p.ProductName, d.Quantity, d.UnitPrice, d.Total FROM SalesDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.SaleID = ?",
                (sale_id,))
            items = [{"name": r[0], "qty": float(r[1] if r[1] else 0), "price": float(r[2] if r[2] else 0),
                      "total": float(r[3] if r[3] else 0)} for r in cursor.fetchall()]
            orders.append(
                {"id": sale_id, "invoice_no": row[1] or "", "customer_name": row[2] or "Unknown", "phone": row[3] or "",
                 "total": float(row[4] if row[4] else 0.0),
                 "date": row[5].strftime('%Y-%m-%d %H:%M') if row[5] else 'N/A', "method": row[6] or "Pending",
                 "customer_code": row[7] or "", "fulfillment_mode": row[8] or "Takeaway"})
        return jsonify({"success": True, "orders": orders})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()


@app.route('/api/settle-sale', methods=['POST'])
def api_settle_sale():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    data = request.get_json()
    order_id, method, split_payments = int(data.get('order_id')), data.get('method', 'Cash'), data.get('split_payments')
    now = datetime.now()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT TotalAmount FROM SalesOrders WHERE SaleID = ?", (order_id,))
        row = cursor.fetchone()
        if not row: return jsonify({"success": False, "message": "Order not found"})
        total = float(row[0] if row[0] else 0.0)

        cursor.execute("UPDATE SalesOrders SET PaymentStatus = 'Paid', PaymentMethod = ? WHERE SaleID = ?",
                       (method, order_id))
        cursor.execute("SELECT PaymentID FROM Payments WHERE SaleID = ?", (order_id,))
        if not cursor.fetchone():
            if split_payments:
                for sp in split_payments: cursor.execute(
                    "INSERT INTO Payments (SaleID, PaymentDate, Amount, Method) VALUES (?, ?, ?, ?)",
                    (order_id, now, sp['amount'], sp['method']))
            else:
                cursor.execute("INSERT INTO Payments (SaleID, PaymentDate, Amount, Method) VALUES (?, ?, ?, ?)",
                               (order_id, now, total, method))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()


@app.route('/search-customer-hub')
def search_customer_hub():
    query = request.args.get('query', '')
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT CustomerCode, CustomerName, Phone, Email, Address FROM Customers WHERE CustomerCode = ? OR CustomerName LIKE ?",
            (query, f"%{query}%"))
        return jsonify({"success": True, "results": [
            {"code": r[0], "name": r[1], "phone": r[2] or "", "email": r[3] or "", "address": r[4] or ""} for r in
            cursor.fetchall()]})
    finally:
        if conn: conn.close()


@app.route('/search-seller-hub')
def search_seller_hub():
    query = request.args.get('query', '')
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT SupplierCode, SupplierName, Phone, Address FROM Suppliers WHERE SupplierCode = ? OR SupplierName LIKE ?",
            (query, f"%{query}%"))
        return jsonify({"success": True,
                        "results": [{"code": r[0], "name": r[1], "phone": r[2] or "", "address": r[3] or ""} for r in
                                    cursor.fetchall()]})
    finally:
        if conn: conn.close()


@app.route('/create-customer', methods=['POST'])
def create_customer():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    data = request.get_json()
    phone = data.get('phone', '').strip()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT CustomerCode FROM Customers WHERE Phone = ?", (phone,))
        if existing := cursor.fetchone(): return jsonify(
            {"success": True, "customer_code": existing[0], "message": "Customer already exists."})

        while True:
            new_code = str(random.randint(100000, 999999))
            cursor.execute("SELECT CustomerID FROM Customers WHERE CustomerCode = ?", (new_code,))
            if not cursor.fetchone(): break
        full_addr = f"{data.get('street', '')}, {data.get('city', '')}, {data.get('state', '')} {data.get('zipcode', '')}, {data.get('country', '')}".strip(
            ', ')
        cursor.execute(
            "INSERT INTO Customers (CustomerCode, CustomerName, Phone, Email, Address) VALUES (?, ?, ?, ?, ?)",
            (new_code, data.get('name', ''), phone, data.get('email', ''), full_addr))
        conn.commit()
        return jsonify({"success": True, "customer_code": new_code})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/update-customer', methods=['POST'])
def update_customer():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    data = request.get_json()
    code = data.get('code', '').strip()
    if not code: return jsonify({"success": False, "message": "Missing Customer Code"})
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        phone, street, city, state, zipcode, country = data.get('phone', ''), data.get('street', ''), data.get('city',
                                                                                                               ''), data.get(
            'state', ''), data.get('zipcode', ''), data.get('country', '')
        full_addr = f"{street}, {city}, {state} {zipcode}, {country}".strip(', ') if city or state else street
        cursor.execute(
            "UPDATE Customers SET CustomerName = ?, Phone = ?, Email = ?, Address = ? WHERE CustomerCode = ?",
            (data.get('name', ''), phone, data.get('email', ''), full_addr, code))
        conn.commit()
        return jsonify({"success": True, "message": "Profile Updated Successfully!"})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/create-seller', methods=['POST'])
def create_seller():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    data = request.get_json()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT SupplierCode FROM Suppliers WHERE Phone = ?", (data.get('phone', ''),))
        if existing := cursor.fetchone(): return jsonify(
            {"success": True, "seller_code": existing[0], "message": "Supplier already exists."})
        while True:
            new_code = str(random.randint(10000, 999999))
            cursor.execute("SELECT SupplierID FROM Suppliers WHERE SupplierCode = ?", (new_code,))
            if not cursor.fetchone(): break
        cursor.execute("INSERT INTO Suppliers (SupplierCode, SupplierName, Phone, Address) VALUES (?, ?, ?, ?)",
                       (new_code, data.get('name', ''), data.get('phone', ''), data.get('address', '')))
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
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE Suppliers SET SupplierName = ?, Phone = ?, Address = ? WHERE SupplierCode = ?",
                       (data.get('name'), data.get('phone'), data.get('address'), code))
        conn.commit()
        return jsonify({"success": True, "message": "Supplier Updated Successfully!"})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/create-employee', methods=['POST'])
def create_employee():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    user_role = session.get('role', 'Staff').lower()
    if user_role not in ['admin', 'manager', 'owner']: return jsonify(
        {"success": False, "message": "Access Denied: Managers or Admins only."}), 403

    data = request.get_json()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        if username and password:
            cursor.execute("SELECT id FROM SystemUsers WHERE username = ?", (username,))
            if cursor.fetchone(): return jsonify(
                {"success": False, "message": f"Username '{username}' already exists. Choose a different one."}), 400
            cursor.execute("INSERT INTO SystemUsers (username, password_plain) VALUES (?, ?)", (username, password))

        name_val = data.get('name', 'Unknown Employee')
        parts = name_val.split(' ', 1)
        cursor.execute(
            "INSERT INTO Employees (FirstName, LastName, Position, Phone, Email, Username) VALUES (?, ?, ?, ?, ?, ?)",
            (parts[0], parts[1] if len(parts) > 1 else '', data.get('role', 'Staff'), data.get('phone', ''), '',
             username))
        cursor.execute("SELECT @@IDENTITY")
        emp_id = int(cursor.fetchone()[0])
        conn.commit()
        msg = "Employee added successfully!"
        if username: msg += " Login credentials generated."
        return jsonify({"success": True, "code": f"EMP-{emp_id}", "message": msg})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/update-employee', methods=['POST'])
def update_employee():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    user_role = session.get('role', 'Staff').lower()
    if user_role not in ['admin', 'manager', 'owner']: return jsonify(
        {"success": False, "message": "Access Denied: Managers or Admins only."}), 403

    data = request.get_json()
    code = data.get('code')
    if not code: return jsonify({"success": False, "message": "Missing ID"})

    emp_id = int(code.split('-')[1])
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        new_username = data.get('username', '').strip()
        new_password = data.get('password', '').strip()
        parts = data.get('name', '').split(' ', 1)
        first_name, last_name = parts[0], parts[1] if len(parts) > 1 else ''
        role, phone = data.get('role', 'Staff'), data.get('phone', '')

        cursor.execute("SELECT Username FROM Employees WHERE EmployeeID = ?", (emp_id,))
        row = cursor.fetchone()
        old_username = row[0] if row else ""

        if new_username:
            if old_username:
                if old_username != new_username:
                    cursor.execute("SELECT id FROM SystemUsers WHERE username = ?", (new_username,))
                    if cursor.fetchone(): return jsonify(
                        {"success": False, "message": "New username already taken."}), 400
                if new_password:
                    cursor.execute("UPDATE SystemUsers SET username = ?, password_plain = ? WHERE username = ?",
                                   (new_username, new_password, old_username))
                else:
                    cursor.execute("UPDATE SystemUsers SET username = ? WHERE username = ?",
                                   (new_username, old_username))
            else:
                cursor.execute("SELECT id FROM SystemUsers WHERE username = ?", (new_username,))
                if cursor.fetchone(): return jsonify({"success": False, "message": "Username already taken."}), 400
                cursor.execute("INSERT INTO SystemUsers (username, password_plain) VALUES (?, ?)",
                               (new_username, new_password))
        else:
            if old_username: cursor.execute("DELETE FROM SystemUsers WHERE username = ?", (old_username,))

        cursor.execute(
            "UPDATE Employees SET FirstName = ?, LastName = ?, Position = ?, Phone = ?, Username = ? WHERE EmployeeID = ?",
            (first_name, last_name, role, phone, new_username, emp_id))
        conn.commit()
        return jsonify({"success": True, "message": "Employee profile updated successfully."})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/delete-employee', methods=['POST'])
def delete_employee():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    user_role = session.get('role', 'Staff').lower()
    if user_role not in ['admin', 'manager', 'owner']: return jsonify(
        {"success": False, "message": "Access Denied: Managers or Admins only."}), 403
    code = request.get_json().get('code')
    if not code: return jsonify({"success": False, "message": "Missing ID"})
    conn = None
    try:
        emp_id = int(code.split('-')[1])
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT Username FROM Employees WHERE EmployeeID = ?", (emp_id,))
        row = cursor.fetchone()
        if row and row[0]: cursor.execute("DELETE FROM SystemUsers WHERE username = ?", (row[0],))
        cursor.execute("DELETE FROM Employees WHERE EmployeeID = ?", (emp_id,))
        conn.commit()
        return jsonify({"success": True, "message": "Employee and Access Rights completely purged."})
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/get-order', methods=['GET'])
def get_order():
    order_id = request.args.get('id', '')
    if not order_id: return jsonify({"success": False, "message": "Order ID required"}), 400
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT c.CustomerName, c.Phone, c.Address, o.TotalAmount, c.CustomerCode, o.PaymentMethod, o.InvoiceNo, o.FulfillmentMode, o.TaxAmount FROM SalesOrders o INNER JOIN Customers c ON o.CustomerID = c.CustomerID WHERE o.SaleID = ?",
            (int(order_id),))
        if not (ord_row := cursor.fetchone()): return jsonify({"success": False, "message": "Not found"}), 404

        cursor.execute(
            "SELECT p.ProductCode, p.ProductName, d.Quantity, d.UnitPrice FROM SalesDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.SaleID = ? ORDER BY d.SaleDetailID ASC",
            (int(order_id),))
        items = [{"code": d[0], "name": d[1], "qty": float(d[2]), "price": float(d[3])} for d in cursor.fetchall()]

        cursor.execute(
            "SELECT ModificationDate, PreviousTotal, PreviousItems FROM OrderHistory WHERE SaleID = ? ORDER BY ModificationDate DESC",
            (int(order_id),))
        history = []
        for h in cursor.fetchall():
            try:
                parsed_items = json.loads(h[2]) if h[2] else []
            except Exception:
                parsed_items = []
            history.append({"date": h[0].strftime('%Y-%m-%d %I:%M %p') if h[0] else 'N/A',
                            "old_total": float(h[1] if h[1] is not None else 0.0), "old_items": parsed_items})

        return jsonify({"success": True,
                        "order": {"id": order_id, "customer_name": ord_row[0], "customer_phone": ord_row[1] or "",
                                  "customer_address": ord_row[2] or "", "items": items,
                                  "total_amount": float(ord_row[3] if ord_row[3] is not None else 0.0),
                                  "customer_code": ord_row[4] or "", "payment_method": ord_row[5] or "Cash",
                                  "invoice_no": ord_row[6] or "", "fulfillment_mode": ord_row[7] or "Takeaway",
                                  "tax_amount": float(ord_row[8] if len(ord_row) > 8 and ord_row[8] else 0),
                                  "history": history}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/get-purchase', methods=['GET'])
def get_purchase():
    pur_id = request.args.get('id', '')
    if not pur_id: return jsonify({"success": False, "message": "Purchase ID required"}), 400
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT s.SupplierName, s.Phone, s.Address, o.TotalAmount, s.SupplierCode, o.InvoiceNo FROM PurchaseOrders o INNER JOIN Suppliers s ON o.SupplierID = s.SupplierID WHERE o.PurchaseID = ?",
            (int(pur_id),))
        if not (pur_row := cursor.fetchone()): return jsonify({"success": False, "message": "Not found"}), 404

        cursor.execute(
            "SELECT p.ProductCode, p.ProductName, d.Quantity, d.UnitPrice FROM PurchaseDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.PurchaseID = ? ORDER BY d.PurchaseDetailID ASC",
            (int(pur_id),))
        items = [{"code": d[0], "name": d[1], "qty": float(d[2]), "price": float(d[3])} for d in cursor.fetchall()]

        return jsonify({"success": True,
                        "purchase": {"id": pur_id, "seller_name": pur_row[0], "seller_phone": pur_row[1] or "",
                                     "seller_address": pur_row[2] or "", "items": items,
                                     "total_amount": float(pur_row[3] if pur_row[3] is not None else 0.0),
                                     "seller_code": pur_row[4] or "", "invoice_no": pur_row[5] or ""}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/update-stock', methods=['POST'])
def update_stock():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    user_role = session.get('role', 'Staff').lower()
    if user_role not in ['admin', 'manager', 'owner']: return jsonify(
        {"success": False, "message": "Access Denied: Managers or Admins only."}), 403
    data = request.get_json()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE Products SET StockQuantity = ? WHERE ProductCode = ?",
                       (float(data['new_stock']), data['code']))
        conn.commit()
        return jsonify({"success": True, "message": "Warehouse stock modified."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/delete-order', methods=['POST'])
def delete_order():
    if not session.get('logged_in'): return jsonify({"success": False}), 401
    user_role = session.get('role', 'Staff').lower()
    if user_role not in ['admin', 'manager', 'owner']: return jsonify(
        {"success": False, "message": "Access Denied: Managers or Admins only."}), 403
    order_id = request.get_json().get('order_id')
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT p.ProductCode, d.Quantity FROM SalesDetails d INNER JOIN Products p ON d.ProductID = p.ProductID WHERE d.SaleID = ?",
            (order_id,))
        for row in cursor.fetchall(): cursor.execute(
            "UPDATE Products SET StockQuantity = StockQuantity + ? WHERE ProductCode = ?", (float(row[1]), row[0]))
        for table in ["OrderHistory", "SalesDetails", "Payments", "SalesOrders"]: cursor.execute(
            f"DELETE FROM {table} WHERE SaleID = ?", (order_id,))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()


if __name__ == '__main__':
    threading.Thread(target=run_daily_backups, daemon=True).start()
    app.run(debug=True, host='0.0.0.0')