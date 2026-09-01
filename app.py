from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
from pathlib import Path
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import csv, io

BASE_DIR = Path(__file__).resolve().parent
DB = BASE_DIR / "database.db"

app = Flask(__name__)
app.secret_key = "change-this-secret-key-in-production"

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('income','expense')),
        amount REAL NOT NULL,
        category TEXT NOT NULL,
        description TEXT,
        date TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)
    conn.commit()
    conn.close()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        if not name or not email or len(password) < 6:
            flash("Enter valid details. Password must be at least 6 characters.", "danger")
            return redirect(url_for("register"))
        conn = get_db()
        try:
            conn.execute("INSERT INTO users(name,email,password) VALUES(?,?,?)",
                         (name,email,generate_password_hash(password)))
            conn.commit()
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email is already registered.", "danger")
            return redirect(url_for("register"))
        finally:
            conn.close()
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()
    uid = session["user_id"]
    totals = conn.execute("""
        SELECT
          COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END),0) income,
          COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END),0) expense
        FROM transactions WHERE user_id=?
    """, (uid,)).fetchone()
    recent = conn.execute("""
        SELECT * FROM transactions WHERE user_id=? ORDER BY date DESC, id DESC LIMIT 8
    """, (uid,)).fetchall()
    categories = conn.execute("""
        SELECT category, SUM(amount) total FROM transactions
        WHERE user_id=? AND type='expense' GROUP BY category ORDER BY total DESC
    """, (uid,)).fetchall()
    monthly = conn.execute("""
        SELECT substr(date,1,7) month,
               SUM(CASE WHEN type='income' THEN amount ELSE 0 END) income,
               SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) expense
        FROM transactions WHERE user_id=? GROUP BY substr(date,1,7) ORDER BY month
    """, (uid,)).fetchall()
    conn.close()
    income, expense = totals["income"], totals["expense"]
    return render_template("dashboard.html", income=income, expense=expense,
                           balance=income-expense, recent=recent,
                           categories=categories, monthly=monthly)

@app.route("/add", methods=["GET","POST"])
@login_required
def add_transaction():
    if request.method == "POST":
        t = request.form["type"]
        amount = float(request.form["amount"])
        category = request.form["category"]
        description = request.form.get("description","").strip()
        date = request.form["date"]
        if amount <= 0:
            flash("Amount must be greater than zero.", "danger")
            return redirect(url_for("add_transaction"))
        conn = get_db()
        conn.execute("""INSERT INTO transactions(user_id,type,amount,category,description,date)
                        VALUES(?,?,?,?,?,?)""",
                     (session["user_id"],t,amount,category,description,date))
        conn.commit(); conn.close()
        flash("Transaction added successfully.", "success")
        return redirect(url_for("dashboard"))
    return render_template("add_transaction.html")

@app.route("/transactions")
@login_required
def transactions():
    q = request.args.get("q","").strip()
    typ = request.args.get("type","")
    conn = get_db()
    sql = "SELECT * FROM transactions WHERE user_id=?"
    args = [session["user_id"]]
    if q:
        sql += " AND (category LIKE ? OR description LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if typ in ("income","expense"):
        sql += " AND type=?"; args.append(typ)
    sql += " ORDER BY date DESC, id DESC"
    rows = conn.execute(sql,args).fetchall()
    conn.close()
    return render_template("transactions.html", transactions=rows, q=q, typ=typ)

@app.route("/delete/<int:tx_id>", methods=["POST"])
@login_required
def delete_transaction(tx_id):
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE id=? AND user_id=?", (tx_id,session["user_id"]))
    conn.commit(); conn.close()
    flash("Transaction deleted.", "success")
    return redirect(url_for("transactions"))

@app.route("/export")
@login_required
def export_csv():
    conn = get_db()
    rows = conn.execute("""SELECT date,type,category,amount,description
                           FROM transactions WHERE user_id=? ORDER BY date DESC""",
                        (session["user_id"],)).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date","Type","Category","Amount","Description"])
    writer.writerows([r["date"],r["type"],r["category"],r["amount"],r["description"] or ""] for r in rows)
    from flask import Response
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=expense_report.csv"})

@app.route("/api/chart-data")
@login_required
def chart_data():
    conn = get_db()
    rows = conn.execute("""SELECT category, SUM(amount) total FROM transactions
                           WHERE user_id=? AND type='expense'
                           GROUP BY category ORDER BY total DESC""",(session["user_id"],)).fetchall()
    conn.close()
    return jsonify({"labels":[r["category"] for r in rows],
                    "values":[r["total"] for r in rows]})

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
