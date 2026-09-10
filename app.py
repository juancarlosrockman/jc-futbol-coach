
from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
from pathlib import Path
from functools import wraps

BASE = Path(__file__).resolve().parent
DB = BASE / "jc_futbol_coach.db"

app = Flask(__name__)
app.secret_key = "change-this-secret-key"

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      role TEXT NOT NULL,
      name TEXT NOT NULL,
      whatsapp TEXT,
      password TEXT,
      dni TEXT
    );
    CREATE TABLE IF NOT EXISTS students(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER,
      parent_name TEXT NOT NULL,
      parent_whatsapp TEXT NOT NULL,
      student_name TEXT NOT NULL,
      age INTEGER NOT NULL,
      zone TEXT,
      mode TEXT,
      place TEXT,
      tariff REAL NOT NULL,
      status TEXT NOT NULL DEFAULT 'active',
      photo_consent TEXT NOT NULL DEFAULT 'No'
    );
    CREATE TABLE IF NOT EXISTS classes(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_id INTEGER,
      date TEXT NOT NULL,
      time TEXT NOT NULL,
      mode TEXT,
      place TEXT,
      amount REAL NOT NULL,
      status TEXT NOT NULL DEFAULT 'scheduled',
      notes TEXT
    );
    CREATE TABLE IF NOT EXISTS payments(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_id INTEGER,
      month TEXT NOT NULL,
      amount REAL NOT NULL,
      method TEXT,
      status TEXT NOT NULL DEFAULT 'paid',
      note TEXT
    );
    CREATE TABLE IF NOT EXISTS waitlist(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      parent_name TEXT NOT NULL,
      whatsapp TEXT NOT NULL,
      student_name TEXT NOT NULL,
      age INTEGER NOT NULL,
      zone TEXT,
      preferences TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      status TEXT DEFAULT 'waiting'
    );
    CREATE TABLE IF NOT EXISTS teams(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      contact TEXT NOT NULL,
      whatsapp TEXT NOT NULL,
      players INTEGER NOT NULL,
      service TEXT NOT NULL,
      place TEXT NOT NULL,
      date TEXT NOT NULL,
      time TEXT NOT NULL,
      amount REAL NOT NULL,
      payment_status TEXT DEFAULT 'pending',
      notes TEXT
    );
    """)

    # Demo accounts/data only. Replace/remove before production.
    if not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        c.execute("INSERT INTO users(role,name,whatsapp,password,dni) VALUES('coach','Entrenador','', '1234', '')")
        p = c.execute("INSERT INTO users(role,name,whatsapp,password,dni) VALUES('parent','Carlos Pérez','999999999','mateo123','12345678')").lastrowid
        s1 = c.execute("""INSERT INTO students(user_id,parent_name,parent_whatsapp,student_name,age,zone,mode,place,tariff,photo_consent)
                          VALUES(?,?,?,?,?,?,?,?,?,?)""",
                       (p,"Carlos Pérez","999999999","Mateo Pérez",11,"San Borja","Parque","Parque de la Familia",60,"Sí")).lastrowid
        s2 = c.execute("""INSERT INTO students(parent_name,parent_whatsapp,student_name,age,zone,mode,place,tariff,photo_consent)
                          VALUES(?,?,?,?,?,?,?,?,?)""",
                       ("Ana García","988888888","Diego García",9,"Surco","Parque","Parque de Surco",70,"No")).lastrowid
        c.execute("""INSERT INTO classes(student_id,date,time,mode,place,amount,status,notes)
                     VALUES(?,?,?,?,?,?,?,?)""",(s1,"2026-09-15","17:00","Parque","Parque de la Familia",60,"scheduled","Trabajo técnico"))
        c.execute("""INSERT INTO classes(student_id,date,time,mode,place,amount,status,notes)
                     VALUES(?,?,?,?,?,?,?,?)""",(s2,"2026-09-17","17:00","Parque","Parque de Surco",70,"scheduled",""))
        c.execute("""INSERT INTO payments(student_id,month,amount,method,status,note)
                     VALUES(?,?,?,?,?,?)""",(s1,"2026-09",480,"Yape","paid","Plan 8 sesiones"))
    c.commit(); c.close()

def coach_required(f):
    @wraps(f)
    def w(*a, **kw):
        if session.get("role") != "coach":
            return redirect(url_for("coach_login"))
        return f(*a, **kw)
    return w

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/nuevo", methods=["GET","POST"])
def nuevo():
    if request.method == "POST":
        mode = request.form["mode"]
        place = request.form["place"]
        if not place.strip():
            flash("Indica el parque o la dirección.")
            return redirect(url_for("nuevo"))
        return redirect(url_for("solicitud_recibida"))
    return render_template("new.html")

@app.route("/espera", methods=["GET","POST"])
def espera():
    if request.method == "POST":
        c=db()
        c.execute("""INSERT INTO waitlist(parent_name,whatsapp,student_name,age,zone,preferences)
                     VALUES(?,?,?,?,?,?)""",
                  (request.form["parent_name"],request.form["whatsapp"],request.form["student_name"],
                   request.form["age"],request.form["zone"],request.form["preferences"]))
        c.commit(); c.close()
        return redirect(url_for("solicitud_recibida"))
    return render_template("waitlist.html")

@app.route("/equipo", methods=["GET","POST"])
def equipo():
    if request.method == "POST":
        c=db()
        c.execute("""INSERT INTO teams(name,contact,whatsapp,players,service,place,date,time,amount,notes)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (request.form["name"],request.form["contact"],request.form["whatsapp"],
                   request.form["players"],request.form["service"],request.form["place"],
                   request.form["date"],request.form["time"],request.form["amount"] or 0,request.form["notes"]))
        c.commit(); c.close()
        return redirect(url_for("solicitud_recibida"))
    return render_template("team.html")

@app.route("/recibida")
def solicitud_recibida():
    return render_template("received.html")

@app.route("/entrenador/login", methods=["GET","POST"])
def coach_login():
    if request.method=="POST":
        c=db(); u=c.execute("SELECT * FROM users WHERE role='coach' AND name=? AND password=?",
                            (request.form["user"],request.form["password"])).fetchone(); c.close()
        if u:
            session["role"]="coach"; session["user_id"]=u["id"]
            return redirect(url_for("dashboard"))
        flash("Usuario o contraseña incorrectos.")
    return render_template("coach_login.html")

@app.route("/entrenador")
@coach_required
def dashboard():
    c=db()
    students=c.execute("SELECT * FROM students WHERE status='active' ORDER BY student_name").fetchall()
    classes=c.execute("""SELECT classes.*,students.student_name FROM classes
                         LEFT JOIN students ON students.id=classes.student_id
                         ORDER BY date,time LIMIT 20""").fetchall()
    wait=c.execute("SELECT * FROM waitlist WHERE status='waiting' ORDER BY id DESC").fetchall()
    teams=c.execute("SELECT * FROM teams ORDER BY date,time LIMIT 10").fetchall()
    c.close()
    return render_template("dashboard.html",students=students,classes=classes,wait=wait,teams=teams)

@app.route("/entrenador/alumnos")
@coach_required
def students():
    c=db(); rows=c.execute("SELECT * FROM students ORDER BY status DESC, student_name").fetchall(); c.close()
    return render_template("students.html",students=rows)

@app.route("/entrenador/alumnos/nuevo", methods=["GET", "POST"])
@coach_required
def new_student():
    if request.method == "POST":
        parent_name = request.form["parent_name"].strip()
        parent_whatsapp = request.form["parent_whatsapp"].strip()
        student_name = request.form["student_name"].strip()
        dni = request.form["dni"].strip()
        if not parent_name or not parent_whatsapp or not student_name or not dni:
            flash("Completa nombre, WhatsApp, alumno y DNI.")
            return redirect(url_for("new_student"))
        c = db()
        u = c.execute("SELECT * FROM users WHERE role='parent' AND whatsapp=?", (parent_whatsapp,)).fetchone()
        if u:
            user_id = u["id"]
            c.execute("UPDATE users SET name=?, password=?, dni=? WHERE id=?", (parent_name, dni, dni, user_id))
        else:
            user_id = c.execute(
                "INSERT INTO users(role,name,whatsapp,password,dni) VALUES('parent',?,?,?,?,?)".replace("VALUES('parent',?,?,?,?,?)", "VALUES('parent',?,?,?,?)"),
                (parent_name, parent_whatsapp, dni, dni)
            ).lastrowid
        c.execute("""INSERT INTO students(user_id,parent_name,parent_whatsapp,student_name,age,zone,mode,place,tariff,photo_consent)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (user_id,parent_name,parent_whatsapp,student_name,int(request.form["age"]),
                   request.form["zone"],request.form["mode"],request.form["place"],
                   float(request.form["tariff"]),request.form["photo_consent"]))
        c.commit(); c.close()
        flash("Alumno creado. Puede ingresar con su WhatsApp y DNI.")
        return redirect(url_for("students"))
    return render_template("new_student.html")

@app.route("/entrenador/alumno/<int:sid>")
@coach_required
def student_detail(sid):
    c=db()
    s=c.execute("SELECT * FROM students WHERE id=?",(sid,)).fetchone()
    classes=c.execute("SELECT * FROM classes WHERE student_id=? ORDER BY date DESC,time DESC",(sid,)).fetchall()
    payments=c.execute("SELECT * FROM payments WHERE student_id=? ORDER BY month DESC",(sid,)).fetchall()
    c.close()
    return render_template("student_detail.html",s=s,classes=classes,payments=payments)

@app.route("/entrenador/clases/nueva", methods=["GET","POST"])
@coach_required
def new_class():
    c=db()
    if request.method=="POST":
        sid=int(request.form["student_id"])
        s=c.execute("SELECT * FROM students WHERE id=?",(sid,)).fetchone()
        c.execute("""INSERT INTO classes(student_id,date,time,mode,place,amount,status,notes)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (sid,request.form["date"],request.form["time"],request.form["mode"],request.form["place"],
                   s["tariff"],"scheduled",request.form["notes"]))
        c.commit(); c.close()
        return redirect(url_for("dashboard"))
    students=c.execute("SELECT * FROM students WHERE status='active' ORDER BY student_name").fetchall()
    c.close()
    return render_template("new_class.html",students=students)

@app.route("/entrenador/pagos", methods=["GET","POST"])
@coach_required
def payments():
    c=db()
    if request.method=="POST":
        c.execute("""INSERT INTO payments(student_id,month,amount,method,status,note)
                     VALUES(?,?,?,?,?,?)""",
                  (request.form["student_id"],request.form["month"],request.form["amount"],
                   request.form["method"],"paid",request.form["note"]))
        c.commit()
    rows=c.execute("""SELECT payments.*,students.student_name FROM payments
                     LEFT JOIN students ON students.id=payments.student_id
                     ORDER BY payments.month DESC,payments.id DESC""").fetchall()
    students=c.execute("SELECT * FROM students WHERE status='active' ORDER BY student_name").fetchall()
    c.close()
    paid=sum(r["amount"] for r in rows if r["status"]=="paid")
    return render_template("payments.html",payments=rows,students=students,paid=paid)

@app.route("/entrenador/disponibilidad", methods=["GET","POST"])
@coach_required
def availability():
    # Simple first version: availability is stored in a session list.
    av=session.get("availability", [
        {"zone":"San Borja","day":"Martes","time":"17:00"},
        {"zone":"San Borja","day":"Jueves","time":"17:00"},
        {"zone":"Surco / Chacarilla","day":"Sábado","time":"10:00"}])
    if request.method=="POST":
        av.append({"zone":request.form["zone"],"day":request.form["day"],"time":request.form["time"]})
        session["availability"]=av
    return render_template("availability.html",availability=av)

@app.route("/entrenador/espera")
@coach_required
def wait_admin():
    c=db(); rows=c.execute("SELECT * FROM waitlist WHERE status='waiting' ORDER BY id DESC").fetchall(); c.close()
    return render_template("wait_admin.html",wait=rows)

@app.route("/entrenador/equipos", methods=["GET","POST"])
@coach_required
def teams():
    c=db()
    if request.method=="POST":
        c.execute("""INSERT INTO teams(name,contact,whatsapp,players,service,place,date,time,amount,payment_status,notes)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (request.form["name"],request.form["contact"],request.form["whatsapp"],request.form["players"],
                   request.form["service"],request.form["place"],request.form["date"],request.form["time"],
                   request.form["amount"] or 0,request.form["payment_status"],request.form["notes"]))
        c.commit()
    rows=c.execute("SELECT * FROM teams ORDER BY date,time").fetchall()
    c.close()
    return render_template("teams.html",teams=rows)

@app.route("/alumno/login", methods=["GET","POST"])
def parent_login():
    if request.method=="POST":
        c=db(); u=c.execute("SELECT * FROM users WHERE role='parent' AND whatsapp=? AND password=?",
                            (request.form["whatsapp"],request.form["password"])).fetchone(); c.close()
        if u:
            session["role"]="parent"; session["user_id"]=u["id"]
            return redirect(url_for("parent_home"))
        flash("Datos incorrectos.")
    return render_template("parent_login.html")

@app.route("/alumno")
def parent_home():
    if session.get("role")!="parent": return redirect(url_for("parent_login"))
    c=db()
    s=c.execute("SELECT * FROM students WHERE user_id=? LIMIT 1",(session["user_id"],)).fetchone()
    classes=c.execute("SELECT * FROM classes WHERE student_id=? ORDER BY date,time",(s["id"],)).fetchall()
    payments=c.execute("SELECT * FROM payments WHERE student_id=? ORDER BY month DESC",(s["id"],)).fetchall()
    c.close()
    return render_template("parent.html",s=s,classes=classes,payments=payments)

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("home"))

@app.context_processor
def globals():
    return {"app_name":"JC Fútbol Coach"}

init_db()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
