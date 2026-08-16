import pyodbc

# Path to your database
DB_PATH = r"C:\Users\modik\OneDrive\Desktop\PZ\PZ_Grain_DB.accdb"
CONN_STRING = f"Driver={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={DB_PATH};"

def create_admin_user():
    conn = None
    try:
        conn = pyodbc.connect(CONN_STRING)
        cursor = conn.cursor()

        username = "kalpen"
        password = "kcmodi"
        role = "Admin"

        # 1. Check if the username already exists in SystemUsers
        cursor.execute("SELECT id FROM SystemUsers WHERE username = ?", (username,))
        if cursor.fetchone():
            print(f"User '{username}' already exists in SystemUsers. Updating password...")
            cursor.execute("UPDATE SystemUsers SET password_plain = ? WHERE username = ?", (password, username))
        else:
            print(f"Creating login for '{username}'...")
            cursor.execute("INSERT INTO SystemUsers (username, password_plain) VALUES (?, ?)", (username, password))

        # 2. Check if the username exists in the Employees table to grant Admin rights
        cursor.execute("SELECT EmployeeID FROM Employees WHERE Username = ?", (username,))
        if cursor.fetchone():
            print(f"Updating '{username}' to {role} role in Employees table...")
            cursor.execute("UPDATE Employees SET Position = ? WHERE Username = ?", (role, username))
        else:
            print(f"Creating Employee profile for '{username}' with {role} privileges...")
            cursor.execute("""
                INSERT INTO Employees (FirstName, LastName, Position, Phone, Email, Username) 
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("Kalpen", "Modi", role, "N/A", "admin@pz.com", username))

        conn.commit()
        print("\n✅ SUCCESS: System Admin 'kalpen' has been granted full access.")
        print("You can now log in with username: kalpen / password: kcmodi")

    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    create_admin_user()