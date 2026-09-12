from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
import re
from urllib.parse import quote
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from functools import wraps
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-this")
DATABASE_URL = os.environ.get("DATABASE_URL")
COACH_WHATSAPP = "51993757225"
DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]
PERU_TZ = ZoneInfo("America/Lima")
MONTHS_ES = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]

PRICING = {
    "new_students": [
        {"age": "3 años", "duration": "30 min", "price": "S/ 50"},
        {"age": "4–5 años", "duration": "45 min", "price": "S/ 60"},
        {"age": "Desde 6 años hasta adultos", "duration": "1 hora", "price": "S/ 70"},
    ],
    "package": {"sessions": 8, "price": 520},
}
SERVICES = [
    "Entrenamiento de equipo",
    "Entrenamiento + dirección de equipo",
    "Celebra tu cumpleaños",
    "Eventos / entrenamientos especiales",
]


def db():
    if not DATABASE_URL:
        raise RuntimeError("Falta la variable de entorno DATABASE_URL.")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        role TEXT NOT NULL, name TEXT NOT NULL, whatsapp TEXT, password TEXT, dni TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS students(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id INTEGER, parent_name TEXT NOT NULL, parent_whatsapp TEXT NOT NULL,
        student_name TEXT NOT NULL, age INTEGER NOT NULL, zone TEXT, mode TEXT, place TEXT,
        tariff DOUBLE PRECISION NOT NULL, status TEXT NOT NULL DEFAULT 'active',
        photo_consent TEXT NOT NULL DEFAULT 'No', dni TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS classes(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, student_id INTEGER,
        date TEXT NOT NULL, time TEXT NOT NULL, mode TEXT, place TEXT,
        amount DOUBLE PRECISION NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled', notes TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, student_id INTEGER,
        month TEXT NOT NULL, amount DOUBLE PRECISION NOT NULL, method TEXT,
        status TEXT NOT NULL DEFAULT 'paid', note TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS waitlist(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, parent_name TEXT NOT NULL,
        whatsapp TEXT NOT NULL, student_name TEXT NOT NULL, age INTEGER NOT NULL,
        zone TEXT, preferences TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'waiting')""")
    c.execute("""CREATE TABLE IF NOT EXISTS teams(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name TEXT NOT NULL,
        contact TEXT NOT NULL, whatsapp TEXT NOT NULL, players INTEGER NOT NULL,
        service TEXT NOT NULL, place TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL,
        amount DOUBLE PRECISION NOT NULL DEFAULT 0, payment_status TEXT DEFAULT 'pending', notes TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS availability(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, zone TEXT NOT NULL,
        day TEXT NOT NULL, time TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE)""")
    # Migrations for versions already deployed.
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS payment_id INTEGER")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS payment_status TEXT NOT NULL DEFAULT 'pending'")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS original_date TEXT")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS original_time TEXT")
    c.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS payment_type TEXT NOT NULL DEFAULT 'regular'")
    c.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS sessions_total INTEGER NOT NULL DEFAULT 1")
    c.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP")
    c.commit()

    coach_user = os.environ.get("COACH_USER", "").strip()
    coach_password = os.environ.get("COACH_PASSWORD", "").strip()
    if coach_user and coach_password:
        existing = c.execute("SELECT id FROM users WHERE role='coach' AND name=%s", (coach_user,)).fetchone()
        if existing:
            c.execute("UPDATE users SET password=%s WHERE id=%s", (coach_password, existing["id"]))
        else:
            c.execute("INSERT INTO users(role,name,whatsapp,password,dni) VALUES(%s,%s,%s,%s,%s)",
                      ("coach", coach_user, "", coach_password, ""))
        c.commit()
    c.close()


def coach_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "coach":
            return redirect(url_for("coach_login"))
        return f(*args, **kwargs)
    return wrapper


def normalize_whatsapp(value):
    return re.sub(r"\D", "", value or "")


def display_date(iso):
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except Exception:
        return iso


def display_day(iso):
    names = DAYS
    try:
        return names[date.fromisoformat(iso).weekday()]
    except Exception:
        return ""


def display_time(t):
    try:
        return datetime.strptime(t, "%H:%M").strftime("%I:%M %p").lstrip("0").replace("AM", "a. m.").replace("PM", "p. m.")
    except Exception:
        return t


def parse_iso_datetime(d, t):
    return datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=PERU_TZ)


def class_status_label(status):
    return {
        "scheduled": "Programada",
        "attended": "Asistida",
        "postponed": "Postergada — pendiente de reprogramación",
        "rescheduled": "Reprogramada",
        "cancelled": "Cancelada",
    }.get(status, status or "Programada")


def service_amount(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


@app.context_processor
def globals():
    return {
        "app_name": "JC Fútbol Coach",
        "coach_name": "Coach Juan Carlos",
        "coach_whatsapp": COACH_WHATSAPP,
        "pricing": PRICING,
        "services": SERVICES,
        "display_date": display_date,
        "display_day": display_day,
        "display_time": display_time,
        "class_status_label": class_status_label,
    }


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/nuevo", methods=["GET", "POST"])
def nuevo():
    c = db()
    if request.method == "POST":
        parent_name = request.form.get("parent_name", "").strip()
        whatsapp = request.form.get("whatsapp", "").strip()
        student_name = request.form.get("student_name", "").strip()
        age = request.form.get("age", "").strip()
        zone = request.form.get("zone", "").strip()
        mode = request.form.get("mode", "").strip()
        place = request.form.get("place", "").strip()
        schedule = request.form.get("schedule", "").strip()
        photo_consent = request.form.get("photo_consent", "").strip()
        if not parent_name or not whatsapp or not student_name or not age or not zone or not place:
            c.close(); flash("Completa los datos obligatorios."); return redirect(url_for("nuevo"))
        message = (
            "⚽ Hola, Coach Juan Carlos. Quiero solicitar un entrenamiento de fútbol.\n\n"
            f"Padre/madre: {parent_name}\nWhatsApp: {whatsapp}\nAlumno: {student_name}\nEdad: {age}\n"
            f"Zona: {zone}\nModalidad: {mode}\nParque o dirección: {place}\n"
            f"Horario de interés: {schedule or 'Por coordinar'}\nFotos/videos: {photo_consent or 'Por coordinar'}"
        )
        c.close()
        return redirect(f"https://wa.me/{COACH_WHATSAPP}?text={quote(message)}")
    availability_rows = c.execute(
        """SELECT * FROM availability WHERE active=TRUE ORDER BY CASE day
        WHEN 'Lunes' THEN 1 WHEN 'Martes' THEN 2 WHEN 'Miércoles' THEN 3 WHEN 'Jueves' THEN 4
        WHEN 'Viernes' THEN 5 WHEN 'Sábado' THEN 6 ELSE 7 END, time, zone"""
    ).fetchall()
    c.close()
    return render_template("new.html", availability=availability_rows)


@app.route("/espera", methods=["GET", "POST"])
def espera():
    if request.method == "POST":
        c = db()
        c.execute("""INSERT INTO waitlist(parent_name,whatsapp,student_name,age,zone,preferences)
                    VALUES(%s,%s,%s,%s,%s,%s)""", (
            request.form["parent_name"], request.form["whatsapp"], request.form["student_name"],
            request.form["age"], request.form.get("zone", ""), request.form.get("preferences", "")))
        c.commit(); c.close(); return redirect(url_for("solicitud_recibida"))
    return render_template("waitlist.html")


@app.route("/equipo", methods=["GET", "POST"])
def equipo():
    if request.method == "POST":
        c = db()
        c.execute("""INSERT INTO teams(name,contact,whatsapp,players,service,place,date,time,amount,payment_status,notes)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,0,'pending',%s)""", (
            request.form["name"], request.form["contact"], request.form["whatsapp"],
            request.form["players"], request.form["service"], request.form["place"],
            request.form["date"], request.form["time"], request.form.get("notes", "")))
        c.commit(); c.close(); return redirect(url_for("solicitud_recibida"))
    return render_template("team.html")


@app.route("/recibida")
def solicitud_recibida():
    return render_template("received.html")


@app.route("/contacto-whatsapp")
def contacto_whatsapp():
    message = request.args.get("mensaje", "Hola, Coach Juan Carlos. Quisiera hacer una consulta.")
    return redirect(f"https://wa.me/{COACH_WHATSAPP}?text={quote(message)}")


@app.route("/entrenador/login", methods=["GET", "POST"])
def coach_login():
    if request.method == "POST":
        c = db(); user = request.form.get("user", "").strip(); password = request.form.get("password", "").strip()
        u = c.execute("SELECT * FROM users WHERE role='coach' AND name=%s AND password=%s", (user, password)).fetchone(); c.close()
        if u:
            session["role"] = "coach"; session["user_id"] = u["id"]; return redirect(url_for("dashboard"))
        flash("Usuario o contraseña incorrectos.")
    return render_template("coach_login.html")


@app.route("/entrenador")
@coach_required
def dashboard():
    c = db(); today = datetime.now(PERU_TZ).date().isoformat()
    students = c.execute("SELECT * FROM students WHERE status='active' ORDER BY student_name").fetchall()
    classes = c.execute("""SELECT classes.*, students.student_name FROM classes
        LEFT JOIN students ON students.id=classes.student_id
        WHERE classes.date >= %s AND classes.status IN ('scheduled','rescheduled','postponed')
        ORDER BY classes.date,classes.time LIMIT 20""", (today,)).fetchall()
    today_classes = c.execute("""SELECT classes.*,students.student_name FROM classes
        LEFT JOIN students ON students.id=classes.student_id WHERE classes.date=%s
        ORDER BY classes.time""", (today,)).fetchall()
    wait_count = c.execute("SELECT COUNT(*) AS n FROM waitlist WHERE status='waiting'").fetchone()["n"]
    pending_payments = c.execute("""SELECT COUNT(*) AS n FROM classes WHERE date >= %s
        AND payment_status='pending' AND status IN ('scheduled','rescheduled','postponed')""", (today,)).fetchone()["n"]
    teams = c.execute("SELECT * FROM teams WHERE date >= %s ORDER BY date,time LIMIT 10", (today,)).fetchall()
    c.close()
    return render_template("dashboard.html", students=students, classes=classes, today_classes=today_classes,
                           wait_count=wait_count, pending_payments=pending_payments, teams=teams)


@app.route("/entrenador/alumnos")
@coach_required
def students():
    c = db(); rows = c.execute("SELECT * FROM students ORDER BY status DESC,student_name").fetchall(); c.close()
    return render_template("students.html", students=rows)


@app.route("/entrenador/alumnos/nuevo", methods=["GET", "POST"])
@coach_required
def new_student():
    c = db()
    if request.method == "POST":
        parent_name = request.form.get("parent_name", "").strip(); parent_whatsapp = request.form.get("parent_whatsapp", "").strip()
        student_name = request.form.get("student_name", "").strip(); dni = request.form.get("dni", "").strip()
        zone = request.form.get("zone", "").strip(); mode = request.form.get("mode", "Parque").strip(); place = request.form.get("place", "").strip()
        photo_consent = request.form.get("photo_consent", "No").strip()
        if not all([parent_name,parent_whatsapp,student_name,dni,zone,place]):
            c.close(); flash("Completa nombre, WhatsApp, alumno, DNI, zona y lugar."); return redirect(url_for("new_student"))
        try: age=int(request.form.get("age","0")); tariff=float(request.form.get("tariff","0"))
        except (ValueError,TypeError):
            c.close(); flash("Edad y tarifa deben ser válidas."); return redirect(url_for("new_student"))
        try:
            u=c.execute("SELECT * FROM users WHERE role='parent' AND whatsapp=%s",(parent_whatsapp,)).fetchone()
            if u:
                user_id=u["id"]; c.execute("UPDATE users SET name=%s,password=%s,dni=%s,whatsapp=%s WHERE id=%s",(parent_name,dni,dni,parent_whatsapp,user_id))
            else:
                user_id=c.execute("INSERT INTO users(role,name,whatsapp,password,dni) VALUES(%s,%s,%s,%s,%s) RETURNING id",("parent",parent_name,parent_whatsapp,dni,dni)).fetchone()["id"]
            sid=c.execute("""INSERT INTO students(user_id,parent_name,parent_whatsapp,student_name,dni,age,zone,mode,place,tariff,photo_consent)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",(user_id,parent_name,parent_whatsapp,student_name,dni,age,zone,mode,place,tariff,photo_consent)).fetchone()["id"]
            created=save_class_groups(c,sid,tariff,request.form)
            payment_id=save_optional_payment(c,sid,request.form,created)
            if payment_id:
                c.execute("UPDATE classes SET payment_id=%s,payment_status='paid' WHERE student_id=%s AND id = ANY(%s)", (payment_id,sid,created))
            c.commit()
        except Exception:
            c.rollback(); c.close(); raise
        c.close(); flash(f"Alumno creado y {len(created)} clase(s) registrada(s)." if created else "Alumno creado. Puedes agregar sus clases desde su ficha.")
        return redirect(url_for("student_detail",sid=sid))
    zones=c.execute("SELECT DISTINCT zone FROM students WHERE zone<>'' ORDER BY zone").fetchall(); c.close()
    return render_template("new_student.html", zones=[z["zone"] for z in zones], days=DAYS)


def save_class_groups(c, sid, tariff, form):
    indexes=sorted({k.rsplit("_",1)[1] for k in form.keys() if k.startswith("class_date_")}, key=lambda x:int(x))
    created=[]
    for idx in indexes:
        d=form.get(f"class_date_{idx}","").strip(); t=form.get(f"class_time_{idx}","").strip()
        if not d or not t: continue
        z=form.get(f"class_zone_{idx}","").strip(); mode=form.get(f"class_mode_{idx}","").strip() or "Parque"
        place=form.get(f"class_place_{idx}","").strip(); notes=form.get(f"class_notes_{idx}","").strip()
        row=c.execute("""INSERT INTO classes(student_id,date,time,mode,place,amount,status,payment_status,original_date,original_time,notes)
            VALUES(%s,%s,%s,%s,%s,%s,'scheduled','pending',%s,%s,%s) RETURNING id""",(sid,d,t,mode,place,tariff,d,t,notes)).fetchone()
        created.append(row["id"])
    return created


def save_optional_payment(c, sid, form, created_ids):
    if form.get("payment_received") != "yes":
        return None
    action=form.get("payment_type","regular")
    if action=="package_existing":
        pid=int(form.get("package_id","0") or 0)
        p=c.execute("SELECT * FROM payments WHERE id=%s AND student_id=%s AND payment_type='package_8' AND status='paid'",(pid,sid)).fetchone()
        if not p:
            return None
        used=c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s AND status='attended'",(pid,)).fetchone()["n"]
        remaining=max(0,(p["sessions_total"] or 8)-used)
        if len(created_ids)>remaining:
            raise ValueError("package_limit")
        c.execute("UPDATE classes SET payment_id=%s,payment_status='paid' WHERE id=ANY(%s)",(pid,created_ids))
        return pid
    amount=service_amount(form.get("payment_amount")); total=1
    if action=="package_8": amount=520.0; total=8
    else: total=max(1,len(created_ids))
    if amount<=0:
        return None
    month=form.get("payment_month") or datetime.now(PERU_TZ).strftime("%Y-%m")
    method=form.get("payment_method") or "No especificado"
    pid=c.execute("""INSERT INTO payments(student_id,month,amount,method,status,note,payment_type,sessions_total,paid_at)
        VALUES(%s,%s,%s,%s,'paid',%s,%s,%s,%s) RETURNING id""",(sid,month,amount,method,form.get("payment_note",""),action,total,datetime.now(PERU_TZ))).fetchone()["id"]
    if created_ids:
        c.execute("UPDATE classes SET payment_id=%s,payment_status='paid' WHERE id=ANY(%s)",(pid,created_ids))
    return pid


@app.route("/entrenador/alumno/<int:sid>/eliminar", methods=["POST"])
@coach_required
def delete_student(sid):
    c=db(); s=c.execute("SELECT * FROM students WHERE id=%s",(sid,)).fetchone()
    if not s: c.close(); flash("Alumno no encontrado."); return redirect(url_for("students"))
    user_id=s["user_id"]
    c.execute("DELETE FROM classes WHERE student_id=%s",(sid,)); c.execute("DELETE FROM payments WHERE student_id=%s",(sid,)); c.execute("DELETE FROM students WHERE id=%s",(sid,))
    if user_id and not c.execute("SELECT 1 FROM students WHERE user_id=%s LIMIT 1",(user_id,)).fetchone(): c.execute("DELETE FROM users WHERE id=%s AND role='parent'",(user_id,))
    c.commit(); c.close(); flash("Alumno eliminado definitivamente junto con sus clases y pagos registrados."); return redirect(url_for("students"))


@app.route("/entrenador/alumno/<int:sid>/editar", methods=["GET","POST"])
@coach_required
def edit_student(sid):
    c=db(); s=c.execute("SELECT * FROM students WHERE id=%s",(sid,)).fetchone()
    if not s: c.close(); flash("Alumno no encontrado."); return redirect(url_for("students"))
    if request.method=="POST":
        parent_name=request.form["parent_name"].strip(); parent_whatsapp=request.form["parent_whatsapp"].strip(); student_name=request.form["student_name"].strip(); dni=request.form["dni"].strip()
        if not parent_name or not parent_whatsapp or not student_name or not dni: c.close(); flash("Completa nombre, WhatsApp, alumno y DNI."); return redirect(url_for("edit_student",sid=sid))
        u=c.execute("SELECT * FROM users WHERE role='parent' AND whatsapp=%s",(parent_whatsapp,)).fetchone()
        if u: user_id=u["id"]; c.execute("UPDATE users SET name=%s,password=%s,dni=%s,whatsapp=%s WHERE id=%s",(parent_name,dni,dni,parent_whatsapp,user_id))
        else: user_id=c.execute("INSERT INTO users(role,name,whatsapp,password,dni) VALUES(%s,%s,%s,%s,%s) RETURNING id",("parent",parent_name,parent_whatsapp,dni,dni)).fetchone()["id"]
        c.execute("""UPDATE students SET user_id=%s,parent_name=%s,parent_whatsapp=%s,student_name=%s,dni=%s,age=%s,zone=%s,mode=%s,place=%s,tariff=%s,photo_consent=%s WHERE id=%s""",(user_id,parent_name,parent_whatsapp,student_name,dni,int(request.form["age"]),request.form["zone"].strip(),request.form["mode"],request.form["place"].strip(),float(request.form["tariff"]),request.form["photo_consent"],sid))
        c.commit(); c.close(); flash("Alumno actualizado correctamente."); return redirect(url_for("student_detail",sid=sid))
    c.close(); return render_template("edit_student.html",s=s)


@app.route("/entrenador/alumno/<int:sid>")
@coach_required
def student_detail(sid):
    c=db(); s=c.execute("SELECT * FROM students WHERE id=%s",(sid,)).fetchone()
    if not s: c.close(); flash("Alumno no encontrado."); return redirect(url_for("students"))
    classes=c.execute("SELECT * FROM classes WHERE student_id=%s ORDER BY date DESC,time DESC",(sid,)).fetchall()
    payments=c.execute("SELECT * FROM payments WHERE student_id=%s ORDER BY month DESC,id DESC",(sid,)).fetchall()
    packages=[]
    for p in payments:
        if p["payment_type"]=="package_8":
            used=c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s AND status='attended'",(p["id"],)).fetchone()["n"]
            pending=max(0,(p["sessions_total"] or 8)-used); packages.append({"payment":p,"used":used,"remaining":pending})
    c.close(); return render_template("student_detail.html",s=s,classes=classes,payments=payments,packages=packages)


@app.route("/entrenador/clase/<int:class_id>/estado", methods=["POST"])
@coach_required
def update_class_status(class_id):
    status=request.form.get("status","").strip()
    allowed={"scheduled","attended","postponed","rescheduled","cancelled"}
    if status not in allowed: flash("Estado no válido."); return redirect(request.referrer or url_for("dashboard"))
    c=db(); row=c.execute("SELECT * FROM classes WHERE id=%s",(class_id,)).fetchone()
    if not row: c.close(); flash("Clase no encontrada."); return redirect(url_for("dashboard"))
    c.execute("UPDATE classes SET status=%s WHERE id=%s",(status,class_id))
    if status=="attended" and row["payment_id"]: c.execute("UPDATE classes SET payment_status='paid' WHERE id=%s",(class_id,))
    c.commit(); c.close(); flash("Estado actualizado."); return redirect(request.referrer or url_for("dashboard"))


@app.route("/entrenador/clase/<int:class_id>/eliminar", methods=["POST"])
@coach_required
def delete_class(class_id):
    c=db(); row=c.execute("SELECT * FROM classes WHERE id=%s",(class_id,)).fetchone()
    if not row: c.close(); flash("Clase no encontrada."); return redirect(url_for("dashboard"))
    c.execute("DELETE FROM classes WHERE id=%s",(class_id,)); c.commit(); c.close(); flash("Clase eliminada."); return redirect(request.referrer or url_for("dashboard"))


@app.route("/entrenador/clases/nueva", methods=["GET","POST"])
@coach_required
def new_class():
    c=db()
    if request.method=="POST":
        sid=int(request.form["student_id"]); s=c.execute("SELECT * FROM students WHERE id=%s AND status='active'",(sid,)).fetchone()
        if not s: c.close(); flash("Alumno no encontrado."); return redirect(url_for("new_class"))
        try:
            created=save_class_groups(c,sid,float(s["tariff"]),request.form)
            if not created: raise ValueError("no_classes")
            pid=save_optional_payment(c,sid,request.form,created)
            if pid: c.execute("UPDATE classes SET payment_id=%s,payment_status='paid' WHERE id=ANY(%s)",(pid,created))
            c.commit()
        except ValueError as exc:
            c.rollback(); c.close()
            msg = "El paquete seleccionado no tiene suficientes clases disponibles." if str(exc)=="package_limit" else "Agrega al menos una fecha y hora válidas."
            flash(msg); return redirect(url_for("new_class",student=sid))
        except Exception:
            c.rollback(); c.close(); raise
        c.close(); flash(f"Se registraron {len(created)} clase(s) correctamente."); return redirect(url_for("student_detail",sid=sid))
    students=c.execute("SELECT * FROM students WHERE status='active' ORDER BY student_name").fetchall(); selected_id=request.args.get("student",type=int)
    selected=c.execute("SELECT * FROM students WHERE id=%s",(selected_id,)).fetchone() if selected_id else None
    zones=c.execute("SELECT DISTINCT zone FROM (SELECT zone FROM students WHERE zone<>'' UNION SELECT zone FROM availability WHERE active=TRUE AND zone<>'') z ORDER BY zone").fetchall()
    packages=c.execute("SELECT * FROM payments WHERE payment_type='package_8' AND status='paid' ORDER BY id DESC").fetchall()
    c.close(); return render_template("new_class.html",students=students,selected=selected,zones=[z["zone"] for z in zones],days=DAYS,packages=packages)


@app.route("/entrenador/pagos", methods=["GET","POST"])
@coach_required
def payments():
    c=db()
    if request.method=="POST":
        sid=request.form["student_id"]; ptype=request.form.get("payment_type","regular"); amount=service_amount(request.form.get("amount"))
        total=1
        if ptype=="package_8": amount=520; total=8
        if amount<=0: c.close(); flash("Ingresa un monto válido."); return redirect(url_for("payments"))
        c.execute("""INSERT INTO payments(student_id,month,amount,method,status,note,payment_type,sessions_total,paid_at)
            VALUES(%s,%s,%s,%s,'paid',%s,%s,%s,%s)""",(sid,request.form["month"],amount,request.form.get("method"),request.form.get("note",""),ptype,total,datetime.now(PERU_TZ)))
        c.commit(); flash("Pago registrado correctamente.")
    rows=c.execute("SELECT payments.*,students.student_name FROM payments LEFT JOIN students ON students.id=payments.student_id ORDER BY payments.month DESC,payments.id DESC").fetchall()
    students=c.execute("SELECT * FROM students WHERE status='active' ORDER BY student_name").fetchall()
    package_info=[]
    for p in rows:
        if p["payment_type"]=="package_8":
            used=c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s AND status='attended'",(p["id"],)).fetchone()["n"]
            package_info.append((p["id"],used,max(0,(p["sessions_total"] or 8)-used)))
    paid=sum(r["amount"] for r in rows if r["status"]=="paid"); c.close()
    return render_template("payments.html",payments=rows,students=students,paid=paid,package_info=dict(package_info))


@app.route("/entrenador/disponibilidad", methods=["GET","POST"])
@coach_required
def availability():
    c=db()
    if request.method=="POST":
        zone=request.form.get("zone","").strip(); day=request.form.get("day","").strip(); time=request.form.get("time","").strip()
        if not zone or not day or not time: c.close(); flash("Completa zona, día y hora."); return redirect(url_for("availability"))
        c.execute("INSERT INTO availability(zone,day,time,active) VALUES(%s,%s,%s,TRUE)",(zone,day,time)); c.commit(); c.close(); flash("Disponibilidad agregada."); return redirect(url_for("availability"))
    av=c.execute("""SELECT * FROM availability WHERE active=TRUE ORDER BY CASE day
        WHEN 'Lunes' THEN 1 WHEN 'Martes' THEN 2 WHEN 'Miércoles' THEN 3 WHEN 'Jueves' THEN 4 WHEN 'Viernes' THEN 5 WHEN 'Sábado' THEN 6 ELSE 7 END,time,zone""").fetchall(); c.close()
    return render_template("availability.html",availability=av,days=DAYS)


@app.route("/entrenador/disponibilidad/eliminar/<int:availability_id>", methods=["POST"])
@coach_required
def delete_availability(availability_id):
    c=db(); c.execute("DELETE FROM availability WHERE id=%s",(availability_id,)); c.commit(); c.close(); flash("Disponibilidad eliminada."); return redirect(url_for("availability"))


@app.route("/entrenador/espera")
@coach_required
def wait_admin():
    c=db(); rows=c.execute("SELECT * FROM waitlist WHERE status='waiting' ORDER BY id DESC").fetchall(); c.close(); return render_template("wait_admin.html",wait=rows)


@app.route("/entrenador/equipos", methods=["GET","POST"])
@coach_required
def teams():
    c=db()
    if request.method=="POST":
        c.execute("""INSERT INTO teams(name,contact,whatsapp,players,service,place,date,time,amount,payment_status,notes)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,0,'pending',%s)""",(request.form["name"],request.form["contact"],request.form["whatsapp"],request.form["players"],request.form["service"],request.form["place"],request.form["date"],request.form["time"],request.form.get("notes","")))
        c.commit(); flash("Solicitud de servicio registrada.")
    rows=c.execute("SELECT * FROM teams ORDER BY date,time").fetchall(); c.close(); return render_template("teams.html",teams=rows)


@app.route("/alumno/login", methods=["GET","POST"])
def parent_login():
    if request.method=="POST":
        whatsapp=normalize_whatsapp(request.form.get("whatsapp","")); password=request.form.get("password","").strip(); c=db()
        candidates=c.execute("SELECT * FROM users WHERE role='parent'").fetchall(); c.close()
        u=next((x for x in candidates if normalize_whatsapp(x["whatsapp"])==whatsapp and x["password"]==password),None)
        if u: session["role"]="parent"; session["user_id"]=u["id"]; return redirect(url_for("parent_home"))
        flash("Datos incorrectos. Usa el WhatsApp registrado y tu contraseña.")
    return render_template("parent_login.html")


@app.route("/alumno/cambiar-contrasena", methods=["GET","POST"])
def change_password():
    if session.get("role")!="parent": return redirect(url_for("parent_login"))
    if request.method=="POST":
        current=request.form.get("current_password","").strip(); new=request.form.get("new_password","").strip(); confirm=request.form.get("confirm_password","").strip()
        if not current or not new or not confirm: flash("Completa todos los campos."); return redirect(url_for("change_password"))
        if new!=confirm: flash("Las nuevas contraseñas no coinciden."); return redirect(url_for("change_password"))
        if len(new)<6: flash("La nueva contraseña debe tener al menos 6 caracteres."); return redirect(url_for("change_password"))
        c=db(); u=c.execute("SELECT * FROM users WHERE id=%s AND role='parent'",(session["user_id"],)).fetchone()
        if not u or u["password"]!=current: c.close(); flash("La contraseña actual es incorrecta."); return redirect(url_for("change_password"))
        c.execute("UPDATE users SET password=%s WHERE id=%s",(new,u["id"])); c.commit(); c.close(); flash("Contraseña actualizada correctamente."); return redirect(url_for("parent_home"))
    return render_template("change_password.html")


@app.route("/alumno")
def parent_home():
    if session.get("role")!="parent": return redirect(url_for("parent_login"))
    c=db(); s=c.execute("SELECT * FROM students WHERE user_id=%s AND status='active' LIMIT 1",(session["user_id"],)).fetchone()
    if not s: c.close(); flash("No hay un alumno asociado a esta cuenta."); return redirect(url_for("logout"))
    today=datetime.now(PERU_TZ).date().isoformat()
    upcoming=c.execute("SELECT * FROM classes WHERE student_id=%s AND (date>=%s OR status='postponed') ORDER BY date,time",(s["id"],today)).fetchall()
    history=c.execute("SELECT * FROM classes WHERE student_id=%s AND date<%s AND status<>'postponed' ORDER BY date DESC,time DESC LIMIT 50",(s["id"],today)).fetchall()
    payments_rows=c.execute("SELECT * FROM payments WHERE student_id=%s ORDER BY month DESC,id DESC",(s["id"],)).fetchall()
    packages=[]
    for p in payments_rows:
        if p["payment_type"]=="package_8":
            used=c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s AND status='attended'",(p["id"],)).fetchone()["n"]
            packages.append({"payment":p,"used":used,"remaining":max(0,(p["sessions_total"] or 8)-used)})
    c.close(); return render_template("parent.html",s=s,upcoming=upcoming,history=history,payments=payments_rows,packages=packages)


@app.route("/alumno/clase/<int:class_id>/postergar", methods=["POST"])
def postpone_class(class_id):
    if session.get("role")!="parent": return redirect(url_for("parent_login"))
    c=db(); current=c.execute("""SELECT classes.*,students.student_name,students.zone AS student_zone
        FROM classes JOIN students ON students.id=classes.student_id WHERE classes.id=%s AND students.user_id=%s""",(class_id,session["user_id"])).fetchone()
    if not current: c.close(); flash("Clase no encontrada."); return redirect(url_for("parent_home"))
    try: original_dt=parse_iso_datetime(current["date"],current["time"])
    except ValueError: c.close(); flash("No se pudo validar la fecha."); return redirect(url_for("parent_home"))
    if datetime.now(PERU_TZ) >= original_dt-timedelta(hours=2):
        c.close(); return redirect(url_for("contacto_whatsapp",mensaje="Hola, Coach Juan Carlos. Tengo una emergencia y necesito coordinar una reprogramación de clase."))
    c.execute("UPDATE classes SET status='postponed' WHERE id=%s",(class_id,)); c.commit(); c.close()
    message=(f"⚽ Hola, Coach Juan Carlos.\n\nEl padre/madre ha solicitado postergar una clase.\n\nAlumno: {current['student_name']}\nZona: {current['student_zone'] or 'Por indicar'}\nClase del día: {display_day(current['date']).lower()} {display_date(current['date'])}\nHora: {display_time(current['time'])}\n\nLa clase quedó pendiente de reprogramación.")
    return redirect(f"https://wa.me/{COACH_WHATSAPP}?text={quote(message)}")


@app.route("/alumno/clase/<int:class_id>/reprogramar", methods=["GET","POST"])
def reschedule(class_id):
    if session.get("role")!="parent": return redirect(url_for("parent_login"))
    c=db(); current=c.execute("""SELECT classes.*,students.student_name,students.zone AS student_zone,students.parent_name
        FROM classes JOIN students ON students.id=classes.student_id WHERE classes.id=%s AND students.user_id=%s""",(class_id,session["user_id"])).fetchone()
    if not current: c.close(); flash("Clase no encontrada."); return redirect(url_for("parent_home"))
    if current["status"] not in ("scheduled","rescheduled","postponed"):
        c.close(); flash("Esta clase no está disponible para reprogramación directa."); return redirect(url_for("parent_home"))
    try: original_dt=parse_iso_datetime(current["date"],current["time"])
    except ValueError: c.close(); flash("No se pudo validar la fecha."); return redirect(url_for("parent_home"))
    now=datetime.now(PERU_TZ)
    if current["status"] != "postponed" and now >= original_dt-timedelta(hours=2):
        c.close(); return redirect(url_for("contacto_whatsapp",mensaje="Hola, Coach Juan Carlos. Tengo una emergencia y necesito coordinar una reprogramación de clase."))
    today=now.date(); monday=today-timedelta(days=today.weekday()); end=monday+timedelta(days=5)
    if request.method=="POST":
        new_date=request.form.get("date","").strip(); new_time=request.form.get("time","").strip(); zone=(current["student_zone"] or "").strip()
        try: selected=date.fromisoformat(new_date); selected_day=DAYS[selected.weekday()]
        except (ValueError,IndexError): c.close(); flash("La fecha seleccionada no es válida."); return redirect(url_for("reschedule",class_id=class_id))
        if selected<today or selected>end: c.close(); flash("Solo puedes elegir horarios disponibles de esta semana."); return redirect(url_for("reschedule",class_id=class_id))
        allowed=c.execute("SELECT 1 FROM availability WHERE active=TRUE AND zone=%s AND day=%s AND time=%s LIMIT 1",(zone,selected_day,new_time)).fetchone()
        occupied=c.execute("""SELECT 1 FROM classes c2 JOIN students s2 ON s2.id=c2.student_id
            WHERE c2.date=%s AND c2.time=%s AND c2.status IN ('scheduled','rescheduled') AND c2.id<>%s AND s2.zone=%s LIMIT 1""",(new_date,new_time,class_id,zone)).fetchone()
        if not allowed or occupied: c.close(); flash("Ese horario ya no está disponible para tu zona."); return redirect(url_for("reschedule",class_id=class_id))
        old_date=current["date"]; old_time=current["time"]
        c.execute("UPDATE classes SET date=%s,time=%s,status='rescheduled',original_date=COALESCE(original_date,%s),original_time=COALESCE(original_time,%s) WHERE id=%s",(new_date,new_time,old_date,old_time,class_id)); c.commit(); c.close()
        message=(f"⚽ Hola, Coach Juan Carlos.\n\nSe reprogramó una clase desde la app.\n\nAlumno: {current['student_name']}\nZona: {zone}\nReprograma clase del día {display_date(old_date)}.\nNueva fecha: {selected_day.lower()} {selected.day} de {selected.strftime('%B').lower()} de {selected.year}.\nNueva hora: {display_time(new_time)}.\n\nLa solicitud fue realizada desde la aplicación de JC Fútbol Coach.")
        return render_template("rescheduled.html",current=current,old_date=old_date,new_date=new_date,new_time=new_time,zone=zone,whatsapp_url=f"https://wa.me/{COACH_WHATSAPP}?text={quote(message)}")
    av=c.execute("SELECT * FROM availability WHERE active=TRUE AND zone=%s ORDER BY CASE day WHEN 'Lunes' THEN 1 WHEN 'Martes' THEN 2 WHEN 'Miércoles' THEN 3 WHEN 'Jueves' THEN 4 WHEN 'Viernes' THEN 5 WHEN 'Sábado' THEN 6 ELSE 7 END,time",(current["student_zone"],)).fetchall()
    slots=[]
    for a in av:
        idx=DAYS.index(a["day"]); d=monday+timedelta(days=idx)
        if today<=d<=end:
            occupied=c.execute("""SELECT 1 FROM classes c2 JOIN students s2 ON s2.id=c2.student_id
                WHERE c2.date=%s AND c2.time=%s AND c2.status IN ('scheduled','rescheduled') AND c2.id<>%s AND s2.zone=%s LIMIT 1""",(d.isoformat(),a["time"],class_id,current["student_zone"])).fetchone()
            if not occupied and not (d.isoformat()==current["date"] and a["time"]==current["time"]): slots.append({"date":d.isoformat(),"day":a["day"],"time":a["time"]})
    c.close(); return render_template("reschedule.html",current=current,slots=slots)


@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("home"))


init_db()

if __name__ == "__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
