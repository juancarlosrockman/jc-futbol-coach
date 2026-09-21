from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
import uuid
import re
from urllib.parse import quote
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from functools import wraps
from secrets import token_hex
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or token_hex(32)
DATABASE_URL = os.environ.get("DATABASE_URL")
COACH_WHATSAPP = "51993757225"
DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]
TURN_ORDER = ["Mañana", "Tarde / noche"]
PERU_TZ = ZoneInfo("America/Lima")
MONTHS_ES = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]

PRICING = {
    "new_students": [
        {"age": "3 años", "duration": "30 min", "price": "S/ 50"},
        {"age": "4–5 años", "duration": "45 min", "price": "S/ 65"},
        {"age": "Desde 6 años hasta adultos", "duration": "1 hora", "price": "S/ 80"},
    ],
    "package": {"sessions": 8, "price": 600},
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


def password_is_hashed(value):
    return isinstance(value, str) and (value.startswith("scrypt:") or value.startswith("pbkdf2:"))

def hash_password(value):
    return generate_password_hash(value)

def password_matches(stored, provided):
    if not stored or provided is None:
        return False
    if password_is_hashed(stored):
        try:
            return check_password_hash(stored, provided)
        except Exception:
            return False
    return stored == provided

def migrate_plaintext_passwords(c):
    rows = c.execute("SELECT id,password FROM users WHERE password IS NOT NULL AND password<>''").fetchall()
    for row in rows:
        if not password_is_hashed(row["password"]):
            c.execute("UPDATE users SET password=%s WHERE id=%s", (hash_password(row["password"]), row["id"]))


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
    c.execute("""CREATE TABLE IF NOT EXISTS availability_status(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, month TEXT NOT NULL,
        turn TEXT NOT NULL, is_full BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE(month, turn))""")
    c.execute("ALTER TABLE availability_status ADD COLUMN IF NOT EXISTS is_full BOOLEAN NOT NULL DEFAULT FALSE")
    c.execute("""CREATE TABLE IF NOT EXISTS coach_notifications(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        kind TEXT NOT NULL,
        class_id INTEGER,
        student_id INTEGER,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_coach_notifications_created ON coach_notifications(created_at DESC)")
    # Migrate the previous version's column named "full" when it exists.
    c.execute("""DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='availability_status' AND column_name='full') THEN
        EXECUTE 'UPDATE availability_status SET is_full = "full"';
      END IF;
    END $$;""")
    # Migrations for versions already deployed.
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS payment_id INTEGER")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS payment_status TEXT NOT NULL DEFAULT 'pending'")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS original_date TEXT")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS original_time TEXT")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS session_group_id TEXT")
    c.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS payment_type TEXT NOT NULL DEFAULT 'regular'")
    c.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS sessions_total INTEGER NOT NULL DEFAULT 1")
    c.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP")
    c.execute("ALTER TABLE availability ADD COLUMN IF NOT EXISTS turn TEXT NOT NULL DEFAULT 'Tarde / noche'")
    c.execute("UPDATE availability SET turn=CASE WHEN CAST(SPLIT_PART(time, ':', 1) AS INTEGER) < 12 THEN 'Mañana' ELSE 'Tarde / noche' END")
    c.execute("CREATE INDEX IF NOT EXISTS idx_classes_student_date ON classes(student_id,date,time)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_classes_date_time ON classes(date,time,status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_payments_student_month ON payments(student_id,month,status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_role_whatsapp ON users(role,whatsapp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_availability_zone_turn ON availability(zone,turn,active)")
    merge_duplicate_parent_accounts(c)
    migrate_plaintext_passwords(c)
    c.commit()

    coach_user = os.environ.get("COACH_USER", "").strip()
    coach_password = os.environ.get("COACH_PASSWORD", "").strip()
    if coach_user and coach_password:
        existing = c.execute("SELECT id FROM users WHERE role='coach' AND name=%s", (coach_user,)).fetchone()
        if existing:
            c.execute("UPDATE users SET password=%s WHERE id=%s", (hash_password(coach_password), existing["id"]))
        else:
            c.execute("INSERT INTO users(role,name,whatsapp,password,dni) VALUES(%s,%s,%s,%s,%s)",
                      ("coach", coach_user, "", hash_password(coach_password), ""))
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


def merge_duplicate_parent_accounts(c):
    """Consolidate accidental duplicate parent accounts sharing one WhatsApp."""
    parents = c.execute("SELECT id, name, whatsapp, password FROM users WHERE role='parent' ORDER BY id").fetchall()
    by_phone = {}
    for u in parents:
        key = normalize_whatsapp(u["whatsapp"])
        if not key:
            continue
        by_phone.setdefault(key, []).append(u)
    for key, group in by_phone.items():
        if len(group) < 2:
            continue
        # Keep the account that already has a password when possible, otherwise the oldest.
        keep = next((u for u in group if u["password"]), group[0])
        for duplicate in group:
            if duplicate["id"] == keep["id"]:
                continue
            c.execute("UPDATE students SET user_id=%s WHERE user_id=%s", (keep["id"], duplicate["id"]))
            c.execute("DELETE FROM users WHERE id=%s AND role='parent'", (duplicate["id"],))


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


def turn_for_time(t):
    try:
        return "Mañana" if int((t or "00:00").split(":", 1)[0]) < 12 else "Tarde / noche"
    except Exception:
        return "Tarde / noche"


def current_week_bounds(ref=None):
    # The coaching week runs Monday through Saturday.
    # On Sunday there is no active coaching week, so the dashboard
    # should show the upcoming Monday-Saturday agenda instead.
    ref = ref or datetime.now(PERU_TZ).date()
    if ref.weekday() == 6:  # Sunday
        monday = ref + timedelta(days=1)
    else:
        monday = ref - timedelta(days=ref.weekday())
    saturday = monday + timedelta(days=5)
    return monday, saturday


def sync_payment_status(c, student_id=None):
    """Assign paid monthly payments or special 8-class packages to scheduled classes.
    The payment amount determines the number of regular classes covered from the
    student's own tariff. Extra classes can remain scheduled with payment pending.
    """
    students=c.execute("SELECT id,tariff FROM students WHERE status='active'" + (" AND id=%s" if student_id else ""), ((student_id,) if student_id else ())).fetchall()
    for st in students:
        sid=st["id"]; tariff=float(st["tariff"] or 0)
        # Preserve existing links and mark those classes paid.
        c.execute("UPDATE classes SET payment_status='paid' WHERE student_id=%s AND payment_id IS NOT NULL", (sid,))
        regular=[]
        for p in c.execute("SELECT id,month,amount,sessions_total FROM payments WHERE student_id=%s AND status='paid' AND payment_type='regular' ORDER BY id", (sid,)).fetchall():
            capacity=int(p["sessions_total"] or ((float(p["amount"] or 0)//tariff) if tariff>0 else 0))
            regular.append({"id":p["id"],"month":p["month"],"remaining":max(0,capacity)})
        packages=[]
        for p in c.execute("SELECT id,sessions_total FROM payments WHERE student_id=%s AND status='paid' AND payment_type='package_8' ORDER BY id", (sid,)).fetchall():
            used=c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s AND status<>'cancelled'", (p["id"],)).fetchone()["n"]
            packages.append({"id":p["id"],"remaining":max(0,(p["sessions_total"] or 8)-used)})
        pending=c.execute("SELECT id,date FROM classes WHERE student_id=%s AND payment_id IS NULL AND status IN ('scheduled','rescheduled','postponed') ORDER BY date,time,id", (sid,)).fetchall()
        for cl in pending:
            month=cl["date"][:7]; linked=False
            for p in regular:
                if p["month"]==month and p["remaining"]>0:
                    c.execute("UPDATE classes SET payment_id=%s,payment_status='paid' WHERE id=%s", (p["id"],cl["id"]))
                    p["remaining"]-=1; linked=True; break
            if linked: continue
            for p in packages:
                if p["remaining"]>0:
                    c.execute("UPDATE classes SET payment_id=%s,payment_status='paid' WHERE id=%s", (p["id"],cl["id"]))
                    p["remaining"]-=1; linked=True; break
            if not linked:
                c.execute("UPDATE classes SET payment_status='pending' WHERE id=%s", (cl["id"],))


def _next_weekday_on_or_after(today, weekday):
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def automatic_recovery_slots(c, student_id=None, today=None, horizon_days=45):
    """Build recovery spaces after the end of each existing weekday/time series."""
    today = today or datetime.now(PERU_TZ).date()
    horizon = today + timedelta(days=horizon_days)
    query = "SELECT DISTINCT date,time FROM classes WHERE status IN ('scheduled','rescheduled','postponed')"
    params = ()
    if student_id is not None:
        query += " AND student_id=%s"
        params = (student_id,)
    rows = c.execute(query, params).fetchall()
    pattern_dates = {}
    for r in rows:
        try:
            d = date.fromisoformat(r["date"])
        except Exception:
            continue
        key = (DAYS[d.weekday()], r["time"])
        pattern_dates.setdefault(key, set()).add(d)

    slots = []
    seen = set()
    now = datetime.now(PERU_TZ)
    for (day_name, tm), dates in pattern_dates.items():
        if len(dates) < 2:
            continue
        last_date = max(dates)
        weekday = DAYS.index(day_name)
        d = _next_weekday_on_or_after(today, weekday)
        while d <= last_date:
            d += timedelta(days=7)
        while d <= horizon:
            if d == today and tm <= now.strftime("%H:%M"):
                d += timedelta(days=7)
                continue
            key = (d.isoformat(), tm)
            occupied = c.execute(
                "SELECT 1 FROM classes WHERE date=%s AND time=%s AND status IN ('scheduled','rescheduled') LIMIT 1",
                (d.isoformat(), tm)
            ).fetchone()
            if not occupied and key not in seen:
                seen.add(key)
                slots.append({
                    "date": d.isoformat(), "day": day_name, "time": tm,
                    "turn": turn_for_time(tm), "kind": "recovery"
                })
            d += timedelta(days=7)
    slots.sort(key=lambda x: (x["date"], x["time"]))
    return slots


def reschedule_slots(c, current, today):
    """Return public and automatic recovery slots for an existing student."""
    today = today if isinstance(today, date) else date.fromisoformat(str(today))
    horizon = today + timedelta(days=45)
    zone = (current["student_zone"] or "").strip()
    slots = []
    seen = set()
    now = datetime.now(PERU_TZ)
    for a in c.execute("SELECT zone,day,time,turn FROM availability WHERE active=TRUE AND zone=%s", (zone,)).fetchall():
        if a["day"] not in DAYS:
            continue
        weekday = DAYS.index(a["day"])
        d = _next_weekday_on_or_after(today, weekday)
        while d <= horizon:
            key = (d.isoformat(), a["time"])
            if d == today and a["time"] <= now.strftime("%H:%M"):
                d += timedelta(days=7)
                continue
            occupied = c.execute(
                "SELECT 1 FROM classes WHERE date=%s AND time=%s AND status IN ('scheduled','rescheduled') AND id<>%s LIMIT 1",
                (d.isoformat(), a["time"], current["id"])
            ).fetchone()
            if not occupied and key not in seen and not (d.isoformat() == current["date"] and a["time"] == current["time"]):
                seen.add(key)
                slots.append({"date": d.isoformat(), "day": a["day"], "time": a["time"], "zone": zone, "turn": a.get("turn") or turn_for_time(a["time"]), "kind": "public"})
            d += timedelta(days=7)

    for x in automatic_recovery_slots(c, current["student_id"], today, 45):
        key = (x["date"], x["time"])
        if key not in seen and not (x["date"] == current["date"] and x["time"] == current["time"]):
            seen.add(key)
            x["zone"] = zone
            slots.append(x)
    slots.sort(key=lambda x: (x["date"], x["time"]))
    return slots


def current_student_slots(c, student, today=None, horizon_days=45):
    """Horarios publicados para un alumno actual, limitados a su zona."""
    today = today or datetime.now(PERU_TZ).date()
    zone = (student["zone"] or "").strip()
    if not zone:
        return []
    now = datetime.now(PERU_TZ)
    slots = []
    seen = set()
    av = c.execute("SELECT zone,day,time,turn FROM availability WHERE active=TRUE AND zone=%s", (zone,)).fetchall()
    for a in av:
        if a["day"] not in DAYS:
            continue
        weekday = DAYS.index(a["day"])
        d = _next_weekday_on_or_after(today, weekday)
        while d <= today + timedelta(days=horizon_days):
            if d == today and a["time"] <= now.strftime("%H:%M"):
                d += timedelta(days=7)
                continue
            key = (d.isoformat(), a["time"])
            occupied = c.execute(
                "SELECT 1 FROM classes WHERE date=%s AND time=%s AND status IN ('scheduled','rescheduled') LIMIT 1",
                (d.isoformat(), a["time"])
            ).fetchone()
            if not occupied and key not in seen:
                seen.add(key)
                slots.append({"date": d.isoformat(), "day": a["day"], "time": a["time"], "turn": a.get("turn") or turn_for_time(a["time"]), "zone": zone})
            d += timedelta(days=7)
    slots.sort(key=lambda x: (x["date"], x["time"]))
    return slots


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
        turn = request.form.get("turn", "").strip()
        mode = request.form.get("mode", "").strip()
        place = request.form.get("place", "").strip()
        schedule = request.form.get("schedule", "").strip()
        photo_consent = request.form.get("photo_consent", "").strip()
        if not parent_name or not whatsapp or not student_name or not age or not zone or not place or not turn:
            c.close(); flash("Completa los datos obligatorios."); return redirect(url_for("nuevo"))
        if turn not in TURN_ORDER:
            c.close(); flash("Completa los datos obligatorios."); return redirect(url_for("nuevo"))
        current_month=datetime.now(PERU_TZ).strftime("%Y-%m")
        closed=c.execute("SELECT is_full FROM availability_status WHERE month=%s AND turn=%s",(current_month,turn)).fetchone()
        if closed and closed["is_full"]:
            c.close(); flash("La agenda de ese turno está llena. Puedes unirte a la lista de espera."); return redirect(url_for("nuevo"))
        schedule_day, schedule_time = "", ""
        if schedule:
            try:
                schedule_day, schedule_time = schedule.split("|", 1)
                datetime.strptime(schedule_time, "%H:%M")
            except (ValueError, TypeError):
                c.close(); flash("Selecciona un horario."); return redirect(url_for("nuevo"))
            valid_slot=c.execute("SELECT 1 FROM availability WHERE active=TRUE AND zone=%s AND day=%s AND time=%s AND turn=%s LIMIT 1",(zone,schedule_day,schedule_time,turn)).fetchone()
            if not valid_slot:
                c.close(); flash("Selecciona un horario."); return redirect(url_for("nuevo"))
        elif turn == "Mañana":
            schedule_day, schedule_time = "Lunes a viernes", "Consultar disponibilidad"
        else:
            c.close(); flash("Selecciona un horario."); return redirect(url_for("nuevo"))
        message = (
            "⚽ Hola, Coach Juan Carlos. Quiero solicitar un entrenamiento de fútbol.\n\n"
            f"Padre/madre: {parent_name}\nWhatsApp: {whatsapp}\nAlumno: {student_name}\nEdad: {age}\n"
            f"Zona: {zone}\nModalidad: {mode}\nParque o dirección: {place}\n"
            f"Turno preferido: {turn}\nHorario de interés: {schedule_day} · {display_time(schedule_time) if schedule_time != "Consultar disponibilidad" else schedule_time}\nFotos/videos: {photo_consent or 'Por coordinar'}"
        )
        c.close()
        return redirect(f"https://wa.me/{COACH_WHATSAPP}?text={quote(message)}")
    availability_rows = c.execute(
        """SELECT * FROM availability WHERE active=TRUE ORDER BY zone, CASE day
        WHEN 'Lunes' THEN 1 WHEN 'Martes' THEN 2 WHEN 'Miércoles' THEN 3 WHEN 'Jueves' THEN 4
        WHEN 'Viernes' THEN 5 WHEN 'Sábado' THEN 6 ELSE 7 END, time"""
    ).fetchall()
    zones = []
    seen = set()
    public_availability = []
    for a in availability_rows:
        if a["zone"] not in seen:
            zones.append(a["zone"]); seen.add(a["zone"])
        public_availability.append({"zone": a["zone"], "day": a["day"], "time": display_time(a["time"]), "raw_time": a["time"], "turn": a.get("turn") or turn_for_time(a["time"])})
    current_month=datetime.now(PERU_TZ).strftime("%Y-%m")
    status_rows=c.execute("SELECT turn,is_full FROM availability_status WHERE month=%s",(current_month,)).fetchall()
    agenda_status={r["turn"]:bool(r["is_full"]) for r in status_rows}
    c.close()
    return render_template("new.html", availability=public_availability, zones=zones, turns=TURN_ORDER, agenda_status=agenda_status)


@app.route("/espera", methods=["GET", "POST"])
def espera():
    if request.method == "POST":
        c = db()
        c.execute("""INSERT INTO waitlist(parent_name,whatsapp,student_name,age,zone,preferences)
                    VALUES(%s,%s,%s,%s,%s,%s)""", (
            request.form["parent_name"], request.form["whatsapp"], request.form["student_name"],
            request.form["age"], request.form.get("zone", ""), request.form.get("preferences", "")))
        c.commit(); c.close(); return redirect(url_for("solicitud_recibida"))
    return render_template("waitlist.html", prefill=request.args)


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
        u = c.execute("SELECT * FROM users WHERE role='coach' AND name=%s", (user,)).fetchone()
        valid = password_matches(u["password"], password) if u else False
        if valid:
            if not password_is_hashed(u["password"]):
                c.execute("UPDATE users SET password=%s WHERE id=%s", (hash_password(password), u["id"])); c.commit()
            c.close(); session["role"] = "coach"; session["user_id"] = u["id"]; return redirect(url_for("dashboard"))
        c.close()
        flash("Usuario o contraseña incorrectos.")
    return render_template("coach_login.html")


@app.route("/entrenador")
@coach_required
def dashboard():
    c = db(); today = datetime.now(PERU_TZ).date().isoformat()
    students = c.execute("SELECT * FROM students WHERE status='active' ORDER BY student_name").fetchall()
    sync_payment_status(c)
    monday, saturday = current_week_bounds(datetime.now(PERU_TZ).date())
    classes = c.execute("""SELECT classes.*, students.student_name FROM classes
        LEFT JOIN students ON students.id=classes.student_id
        WHERE classes.date > %s AND classes.date <= %s AND classes.status IN ('scheduled','rescheduled','postponed')
        ORDER BY classes.date,classes.time LIMIT 50""", (today, saturday.isoformat())).fetchall()
    today_classes = c.execute("""SELECT classes.*,students.student_name FROM classes
        LEFT JOIN students ON students.id=classes.student_id WHERE classes.date=%s
        ORDER BY classes.time""", (today,)).fetchall()
    notifications = c.execute("""SELECT n.*, s.student_name
        FROM coach_notifications n
        LEFT JOIN students s ON s.id=n.student_id
        ORDER BY n.created_at DESC, n.id DESC LIMIT 10""").fetchall()
    wait_count = c.execute("SELECT COUNT(*) AS n FROM waitlist WHERE status='waiting'").fetchone()["n"]
    # A class is pending only when it has no paid package/payment coverage.
    # Legacy classes without a payment_id are matched against paid regular
    # payments in the same calendar month, preventing false pending counts.
    pending_payments = 0
    for s in students:
        future = c.execute("""SELECT id,date,payment_id,payment_status FROM classes
            WHERE student_id=%s AND date >= %s AND status IN ('scheduled','rescheduled','postponed')
            ORDER BY date,time""", (s["id"], today)).fetchall()
        if not future:
            continue
        tariff = float(s["tariff"] or 0)
        regular_capacity = {}
        if tariff > 0:
            paid_regular_rows = c.execute("""SELECT month, COALESCE(SUM(amount),0) AS total
                FROM payments WHERE student_id=%s AND status='paid' AND payment_type='regular'
                GROUP BY month""", (s["id"],)).fetchall()
            for p in paid_regular_rows:
                regular_capacity[p["month"]] = int((float(p["total"] or 0)) // tariff)
        package_remaining = 0
        package_rows = c.execute("""SELECT id,sessions_total FROM payments
            WHERE student_id=%s AND status='paid' AND payment_type='package_8' ORDER BY id""", (s["id"],)).fetchall()
        for p in package_rows:
            assigned = c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s", (p["id"],)).fetchone()["n"]
            package_remaining += max(0, (p["sessions_total"] or 8) - assigned)
        for x in future:
            if x["payment_id"] is not None or x["payment_status"] == "paid":
                continue
            month = x["date"][:7]
            if regular_capacity.get(month, 0) > 0:
                regular_capacity[month] -= 1
            elif package_remaining > 0:
                package_remaining -= 1
            else:
                pending_payments += 1
    teams = c.execute("SELECT * FROM teams WHERE date >= %s ORDER BY date,time LIMIT 10", (today,)).fetchall()
    c.close()
    return render_template("dashboard.html", students=students, classes=classes, today_classes=today_classes,
                           wait_count=wait_count, pending_payments=pending_payments, teams=teams, notifications=notifications)


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
        parent_name = request.form.get("parent_name", "").strip()
        parent_whatsapp = normalize_whatsapp(request.form.get("parent_whatsapp", ""))
        student_name = request.form.get("student_name", "").strip()
        dni = request.form.get("dni", "").strip()
        zone = request.form.get("zone", "").strip()
        mode = request.form.get("mode", "Parque").strip()
        place = request.form.get("place", "").strip()
        photo_consent = request.form.get("photo_consent", "No, no autorizo").strip()
        parent_password = request.form.get("parent_password", "").strip()
        if not all([parent_name, parent_whatsapp, student_name, dni, zone, place]):
            c.close(); flash("Completa nombre, WhatsApp, alumno, DNI, zona y lugar."); return redirect(url_for("new_student"))
        try:
            age = int(request.form.get("age", "0"))
            tariff = float(request.form.get("tariff", "0"))
        except (ValueError, TypeError):
            c.close(); flash("Edad y tarifa deben ser válidas."); return redirect(url_for("new_student"))
        if age < 3 or tariff < 0:
            c.close(); flash("Revisa la edad y la tarifa."); return redirect(url_for("new_student"))
        try:
            # One parent account can have several children. Never replace the
            # existing password when the WhatsApp already belongs to a parent.
            parent_candidates = c.execute("SELECT * FROM users WHERE role='parent' ORDER BY id").fetchall()
            u = next((x for x in parent_candidates if normalize_whatsapp(x["whatsapp"]) == parent_whatsapp), None)
            if u:
                user_id = u["id"]
                c.execute("UPDATE users SET name=%s,whatsapp=%s WHERE id=%s", (parent_name, parent_whatsapp, user_id))
                password_notice = None
            else:
                if not parent_password:
                    import secrets
                    parent_password = "JC" + token_hex(3).upper()
                user_id = c.execute(
                    "INSERT INTO users(role,name,whatsapp,password,dni) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                    ("parent", parent_name, parent_whatsapp, hash_password(parent_password), "")
                ).fetchone()["id"]
                password_notice = parent_password
            sid = c.execute("""INSERT INTO students(user_id,parent_name,parent_whatsapp,student_name,dni,age,zone,mode,place,tariff,photo_consent)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (user_id,parent_name,parent_whatsapp,student_name,dni,age,zone,mode,place,tariff,photo_consent)).fetchone()["id"]
            created = save_class_groups(c, sid, tariff, request.form)
            payment_id = save_optional_payment(c, sid, request.form, created)
            c.commit()
        except ValueError as exc:
            c.rollback(); c.close()
            msg = ("El paquete seleccionado no existe o ya no tiene clases disponibles." if str(exc)=="package_invalid" else ("Agrega al menos una fecha y hora válidas." if str(exc)=="invalid_class" else ("Ese horario no está disponible para reprogramar." if str(exc) in ("occupied_class", "duplicate_class") else "Revisa los datos y el monto del pago.")))
            flash(msg); return redirect(url_for("new_student"))
        except Exception:
            c.rollback(); c.close()
            flash("No se pudo completar el registro. No repitas el pago sin revisar primero si el alumno ya fue creado.")
            return redirect(url_for("new_student"))
        c.close()
        if password_notice:
            flash(f"Alumno creado. Contraseña inicial del padre/madre: {password_notice}")
        elif created:
            flash(f"Alumno agregado a la cuenta existente y {len(created)} clase(s) registrada(s).")
        else:
            flash("Alumno agregado a la cuenta existente. Puedes agregar sus clases desde su ficha.")
        return redirect(url_for("student_detail", sid=sid))
    c.close()
    return render_template("new_student.html", days=DAYS)


def save_class_groups(c, sid, tariff, form):
    indexes=sorted({k.rsplit("_",1)[1] for k in form.keys() if k.startswith("class_date_")}, key=lambda x:int(x))
    created=[]
    submitted=set()
    today=datetime.now(PERU_TZ).date()
    student_place = c.execute("SELECT mode, place FROM students WHERE id=%s", (sid,)).fetchone()
    mode=(student_place["mode"] if student_place else "Parque") or "Parque"
    place=(student_place["place"] if student_place else "") or ""
    for idx in indexes:
        d=form.get(f"class_date_{idx}","").strip(); t=form.get(f"class_time_{idx}","").strip()
        if not d and not t:
            continue
        try:
            class_date=date.fromisoformat(d)
            datetime.strptime(t, "%H:%M")
        except (ValueError, TypeError):
            raise ValueError("invalid_class")
        # Coach Juan Carlos can register past classes because the panel is also
        # used to record classes already taken and payments already received.
        if class_date.weekday() > 5:
            raise ValueError("invalid_class")
        key=(d,t)
        if key in submitted:
            raise ValueError("duplicate_class")
        submitted.add(key)
        # A time slot may contain more than one student when they train together
        # (siblings, cousins, or another small group). Each student keeps their
        # own classes and payments. Classes sharing date/time/mode/place are
        # linked to the same session_group_id.
        existing=c.execute("SELECT id,session_group_id FROM classes WHERE date=%s AND time=%s AND mode=%s AND place=%s AND status IN ('scheduled','rescheduled','postponed','attended') ORDER BY id LIMIT 1",(d,t,mode,place)).fetchone()
        if existing and existing["session_group_id"]:
            session_group_id=existing["session_group_id"]
        elif existing:
            session_group_id=str(uuid.uuid4())
            c.execute("UPDATE classes SET session_group_id=%s WHERE date=%s AND time=%s AND mode=%s AND place=%s AND status IN ('scheduled','rescheduled','postponed','attended')",(session_group_id,d,t,mode,place))
        else:
            session_group_id=str(uuid.uuid4())
        notes=form.get(f"class_notes_{idx}","").strip()
        row=c.execute("""INSERT INTO classes(student_id,date,time,mode,place,amount,status,payment_status,original_date,original_time,notes,session_group_id)
            VALUES(%s,%s,%s,%s,%s,%s,'scheduled','pending',%s,%s,%s,%s) RETURNING id""",(sid,d,t,mode,place,tariff,d,t,notes,session_group_id)).fetchone()
        created.append(row["id"])
    return created


def save_optional_payment(c, sid, form, created_ids):
    if form.get("payment_received") != "yes":
        return None
    ptype = form.get("payment_type", "regular")
    month = form.get("payment_month") or datetime.now(PERU_TZ).strftime("%Y-%m")
    method = form.get("payment_method") or "No especificado"
    if ptype == "package_existing":
        pid = int(form.get("package_id", "0") or 0)
        p = c.execute("SELECT * FROM payments WHERE id=%s AND student_id=%s AND payment_type='package_8' AND status='paid'", (pid, sid)).fetchone()
        if not p:
            raise ValueError("package_invalid")
        used = c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s", (pid,)).fetchone()["n"]
        remaining = max(0, (p["sessions_total"] or 8) - used)
        ids = created_ids[:remaining]
        if ids:
            c.execute("UPDATE classes SET payment_id=%s,payment_status='paid' WHERE id=ANY(%s)", (pid, ids))
        return pid
    if ptype == "package_8":
        amount, total = 600.0, 8
    else:
        amount = service_amount(form.get("payment_amount"))
        if amount <= 0:
            raise ValueError("payment_amount")
        tariff_row = c.execute("SELECT tariff FROM students WHERE id=%s", (sid,)).fetchone()
        tariff = float(tariff_row["tariff"] or 0) if tariff_row else 0
        total = int(amount // tariff) if tariff > 0 else 0
        if total < 1:
            raise ValueError("payment_amount")
    pid = c.execute("""INSERT INTO payments(student_id,month,amount,method,status,note,payment_type,sessions_total,paid_at)
        VALUES(%s,%s,%s,%s,'paid',%s,%s,%s,%s) RETURNING id""",
        (sid, month, amount, method, form.get("payment_note", ""), ptype, total, datetime.now(PERU_TZ))).fetchone()["id"]
    if created_ids:
        if ptype == "package_8":
            ids = created_ids[:total]
        else:
            ids = [cid for cid in created_ids if c.execute("SELECT date FROM classes WHERE id=%s", (cid,)).fetchone()["date"].startswith(month)][:total]
        if ids:
            c.execute("UPDATE classes SET payment_id=%s,payment_status='paid' WHERE id=ANY(%s)", (pid, ids))
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


@app.route("/entrenador/alumno/<int:sid>/editar", methods=["GET", "POST"])
@coach_required
def edit_student(sid):
    c = db(); s = c.execute("SELECT * FROM students WHERE id=%s", (sid,)).fetchone()
    if not s:
        c.close(); flash("Alumno no encontrado."); return redirect(url_for("students"))
    if request.method == "POST":
        parent_name=request.form["parent_name"].strip(); parent_whatsapp=normalize_whatsapp(request.form["parent_whatsapp"]); student_name=request.form["student_name"].strip(); dni=request.form["dni"].strip()
        if not parent_name or not parent_whatsapp or not student_name or not dni:
            c.close(); flash("Completa nombre, WhatsApp, alumno y DNI."); return redirect(url_for("edit_student",sid=sid))
        try:
            age=int(request.form["age"]); tariff=float(request.form["tariff"])
        except (ValueError,TypeError):
            c.close(); flash("Edad y tarifa deben ser válidas."); return redirect(url_for("edit_student",sid=sid))
        parent_candidates=c.execute("SELECT * FROM users WHERE role='parent' ORDER BY id").fetchall()
        u=next((x for x in parent_candidates if normalize_whatsapp(x["whatsapp"])==parent_whatsapp),None)
        if u:
            user_id=u["id"]
            c.execute("UPDATE users SET name=%s,whatsapp=%s WHERE id=%s",(parent_name,parent_whatsapp,user_id))
        else:
            # If the original account belongs only to this student, keep it and update its identity.
            user_id=s["user_id"]
            if user_id:
                c.execute("UPDATE users SET name=%s,whatsapp=%s WHERE id=%s",(parent_name,parent_whatsapp,user_id))
            else:
                temporary_password = "JC" + token_hex(3).upper()
                user_id=c.execute("INSERT INTO users(role,name,whatsapp,password,dni) VALUES(%s,%s,%s,%s,%s) RETURNING id",("parent",parent_name,parent_whatsapp,hash_password(temporary_password),dni)).fetchone()["id"]
        c.execute("""UPDATE students SET user_id=%s,parent_name=%s,parent_whatsapp=%s,student_name=%s,dni=%s,age=%s,zone=%s,mode=%s,place=%s,tariff=%s,photo_consent=%s WHERE id=%s""",
                  (user_id,parent_name,parent_whatsapp,student_name,dni,age,request.form["zone"].strip(),request.form["mode"],request.form["place"].strip(),tariff,request.form["photo_consent"],sid))
        c.commit(); c.close(); flash("Alumno actualizado correctamente."); return redirect(url_for("student_detail",sid=sid))
    c.close(); return render_template("edit_student.html",s=s)


@app.route("/entrenador/alumno/<int:sid>/restablecer-contrasena", methods=["POST"])
@coach_required
def reset_parent_password(sid):
    c=db()
    s=c.execute("SELECT * FROM students WHERE id=%s", (sid,)).fetchone()
    if not s:
        c.close(); flash("Alumno no encontrado."); return redirect(url_for("students"))
    user_id=s["user_id"]
    if not user_id:
        c.close(); flash("Este alumno no tiene una cuenta de padre/madre asociada."); return redirect(url_for("student_detail",sid=sid))
    temporary_password="JC" + token_hex(3).upper()
    c.execute("UPDATE users SET password=%s WHERE id=%s AND role='parent'", (hash_password(temporary_password), user_id))
    c.commit(); c.close()
    flash(f"Nueva contraseña temporal para {s['parent_name']}: {temporary_password}. Entrégasela al padre/madre; no se mostrará nuevamente.")
    return redirect(url_for("student_detail",sid=sid))


@app.route("/entrenador/alumno/<int:sid>")
@coach_required
def student_detail(sid):
    c = db()
    sync_payment_status(c, sid)
    c.commit(); s = c.execute("SELECT * FROM students WHERE id=%s", (sid,)).fetchone()
    if not s:
        c.close(); flash("Alumno no encontrado."); return redirect(url_for("students"))
    classes = c.execute("SELECT * FROM classes WHERE student_id=%s ORDER BY date DESC,time DESC", (sid,)).fetchall()
    payments = c.execute("SELECT * FROM payments WHERE student_id=%s ORDER BY month DESC,id DESC", (sid,)).fetchall()
    active_classes=[x for x in classes if x["status"]!='cancelled']
    summary={
        "scheduled": len(active_classes),
        "paid": sum(1 for x in active_classes if x["payment_status"]=='paid'),
        "attended": sum(1 for x in active_classes if x["status"]=='attended'),
        "pending": sum(1 for x in active_classes if x["payment_status"]!='paid'),
    }
    packages = []
    for p in payments:
        if p["payment_type"] == "package_8":
            assigned = c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s", (p["id"],)).fetchone()["n"]
            attended = c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s AND status='attended'", (p["id"],)).fetchone()["n"]
            packages.append({"payment": p, "assigned": assigned, "attended": attended, "remaining": max(0, (p["sessions_total"] or 8) - assigned)})
    c.close(); return render_template("student_detail.html", s=s, classes=classes, payments=payments, packages=packages, summary=summary)


@app.route("/entrenador/clase/<int:class_id>/editar", methods=["GET", "POST"])
@coach_required
def edit_class(class_id):
    c = db()
    current = c.execute("""SELECT classes.*, students.student_name, students.zone AS student_zone
        FROM classes JOIN students ON students.id=classes.student_id WHERE classes.id=%s""", (class_id,)).fetchone()
    if not current:
        c.close(); flash("Clase no encontrada."); return redirect(url_for("dashboard"))
    if current["status"] in ("attended", "cancelled"):
        c.close(); flash("Esta clase no se puede editar porque ya está cerrada."); return redirect(request.referrer or url_for("dashboard"))
    if request.method == "POST":
        new_date = request.form.get("date", "").strip()
        new_time = request.form.get("time", "").strip()
        try:
            selected = date.fromisoformat(new_date)
            datetime.strptime(new_time, "%H:%M")
        except (ValueError, TypeError):
            c.close(); flash("La fecha u hora no son válidas."); return redirect(url_for("edit_class", class_id=class_id))
        if selected.weekday() > 5:
            c.close(); flash("No se pueden agendar clases los domingos."); return redirect(url_for("edit_class", class_id=class_id))
        conflict = c.execute("""SELECT id,student_id,mode,place,session_group_id FROM classes
            WHERE date=%s AND time=%s AND id<>%s
            AND status IN ('scheduled','rescheduled','postponed') LIMIT 1""",
            (new_date, new_time, class_id)).fetchone()
        if conflict:
            same_group = bool(current.get("session_group_id") and conflict.get("session_group_id") == current.get("session_group_id"))
            same_student = conflict["student_id"] == current["student_id"]
            if not (same_group or same_student):
                c.close(); flash("Ese horario ya está ocupado por otra actividad."); return redirect(url_for("edit_class", class_id=class_id))
        old_date, old_time = current["date"], current["time"]
        c.execute("UPDATE classes SET date=%s,time=%s WHERE id=%s", (new_date, new_time, class_id))
        c.commit(); c.close()
        if old_date != new_date or old_time != new_time:
            flash(f"Clase corregida: {current['student_name']} · {display_date(new_date)} · {display_time(new_time)}.")
        else:
            flash("Clase guardada sin cambios de horario.")
        return redirect(request.form.get("return_to") or url_for("student_detail", sid=current["student_id"]))
    c.close()
    return render_template("edit_class.html", current=current, return_to=request.args.get("return_to", ""))


@app.route("/entrenador/clase/<int:class_id>/estado", methods=["POST"])
@coach_required
def update_class_status(class_id):
    status=request.form.get("status","").strip()
    allowed={"scheduled","attended","postponed","rescheduled","cancelled"}
    if status not in allowed: flash("Estado no válido."); return redirect(request.referrer or url_for("dashboard"))
    c=db(); row=c.execute("SELECT * FROM classes WHERE id=%s",(class_id,)).fetchone()
    if not row: c.close(); flash("Clase no encontrada."); return redirect(url_for("dashboard"))
    if status=="cancelled":
        # A cancelled class should not keep consuming a payment. This lets the
        # payment cover the correct class if the Coach is fixing a scheduling error.
        c.execute("UPDATE classes SET status=%s,payment_id=NULL,payment_status='pending' WHERE id=%s",(status,class_id))
    else:
        c.execute("UPDATE classes SET status=%s WHERE id=%s",(status,class_id))
        if status=="attended" and row["payment_id"]: c.execute("UPDATE classes SET payment_status='paid' WHERE id=%s",(class_id,))
        sync_payment_status(c, row["student_id"])
    c.commit(); c.close(); flash("Clase anulada." if status=="cancelled" else "Estado actualizado."); return redirect(request.referrer or url_for("dashboard"))


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
        try:
            sid=int(request.form.get("student_id",""))
        except (TypeError,ValueError):
            c.close(); flash("Alumno no encontrado."); return redirect(url_for("new_class"))
        s=c.execute("SELECT * FROM students WHERE id=%s AND status='active'",(sid,)).fetchone()
        if not s: c.close(); flash("Alumno no encontrado."); return redirect(url_for("new_class"))
        try:
            created=save_class_groups(c,sid,float(s["tariff"]),request.form)
            if not created: raise ValueError("no_classes")
            pid=save_optional_payment(c,sid,request.form,created)
            c.commit()
        except ValueError as exc:
            c.rollback(); c.close()
            msg = ("El paquete seleccionado no tiene suficientes clases disponibles." if str(exc)=="package_limit" else ("No se pudo registrar el pago. Revisa el monto." if str(exc) in ("payment_amount", "package_invalid") else ("Agrega al menos una fecha y hora válidas." if str(exc)=="invalid_class" else ("Ese horario no está disponible para reprogramar." if str(exc) in ("occupied_class", "duplicate_class") else "Agrega al menos una fecha y hora válidas."))))
            flash(msg); return redirect(url_for("new_class",student=sid))
        except Exception:
            c.rollback(); c.close(); flash("No se pudieron registrar las clases. Revisa los datos antes de volver a intentarlo."); return redirect(url_for("new_class",student=sid))
        c.close(); flash(f"Se registraron {len(created)} clase(s) correctamente."); return redirect(url_for("student_detail",sid=sid))
    students=c.execute("SELECT * FROM students WHERE status='active' ORDER BY student_name").fetchall(); selected_id=request.args.get("student",type=int)
    selected=c.execute("SELECT * FROM students WHERE id=%s",(selected_id,)).fetchone() if selected_id else None
    zones=c.execute("SELECT DISTINCT zone FROM (SELECT zone FROM students WHERE zone<>'' UNION SELECT zone FROM availability WHERE active=TRUE AND zone<>'') z ORDER BY zone").fetchall()
    packages=c.execute("SELECT * FROM payments WHERE student_id=%s AND payment_type='package_8' AND status='paid' ORDER BY id DESC", (selected_id or -1,)).fetchall()
    c.close(); return render_template("new_class.html",students=students,selected=selected,zones=[z["zone"] for z in zones],days=DAYS,packages=packages)


@app.route("/entrenador/pagos", methods=["GET","POST"])
@coach_required
def payments():
    c=db()
    sync_payment_status(c)
    if request.method=="POST":
        sid=request.form.get("student_id","").strip(); ptype=request.form.get("payment_type","regular"); amount=service_amount(request.form.get("amount"))
        if not sid.isdigit():
            c.close(); flash("Alumno no encontrado."); return redirect(url_for("payments"))
        payment_month=request.form.get("month") or request.form.get("package_month") or datetime.now(PERU_TZ).strftime("%Y-%m")
        tariff_row=c.execute("SELECT tariff FROM students WHERE id=%s AND status='active'",(sid,)).fetchone()
        tariff=float(tariff_row["tariff"] or 0) if tariff_row else 0
        if ptype=="package_8": amount=600.0; total=8
        else:
            if amount<=0 or tariff<=0: c.close(); flash("El monto debe cubrir al menos una clase según la tarifa del alumno."); return redirect(url_for("payments"))
            total=int(amount//tariff)
            if total<1: c.close(); flash("El monto debe cubrir al menos una clase según la tarifa del alumno."); return redirect(url_for("payments"))
        pid=c.execute("""INSERT INTO payments(student_id,month,amount,method,status,note,payment_type,sessions_total,paid_at)
            VALUES(%s,%s,%s,%s,'paid',%s,%s,%s,%s) RETURNING id""",(sid,payment_month,amount,request.form.get("method"),request.form.get("note",""),ptype,total,datetime.now(PERU_TZ))).fetchone()["id"]
        c.commit(); sync_payment_status(c); c.commit(); flash(f"Pago registrado correctamente. Cubre {total} clase(s).")
        c.close(); return redirect(url_for("payments"))
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
    current_month=datetime.now(PERU_TZ).strftime("%Y-%m")
    if request.method=="POST":
        action=request.form.get("action", "add")
        if action == "set_full":
            turn=request.form.get("turn", "").strip()
            if turn in TURN_ORDER:
                c.execute("INSERT INTO availability_status(month,turn,is_full) VALUES(%s,%s,TRUE) ON CONFLICT(month,turn) DO UPDATE SET is_full=TRUE",(current_month,turn))
                c.commit(); c.close(); flash(f"Agenda marcada como llena para {turn.lower()}."); return redirect(url_for("availability"))
        if action == "open":
            turn=request.form.get("turn", "").strip()
            if turn in TURN_ORDER:
                c.execute("INSERT INTO availability_status(month,turn,is_full) VALUES(%s,%s,FALSE) ON CONFLICT(month,turn) DO UPDATE SET is_full=FALSE",(current_month,turn))
                c.commit(); c.close(); flash(f"Agenda disponible nuevamente para {turn.lower()}."); return redirect(url_for("availability"))
        zone=request.form.get("zone","").strip(); day=request.form.get("day","").strip(); time=request.form.get("time","").strip()
        turn=request.form.get("turn","").strip() or turn_for_time(time)
        if not zone or not day or not time: c.close(); flash("Completa zona, día y hora."); return redirect(url_for("availability"))
        try: datetime.strptime(time, "%H:%M")
        except ValueError: c.close(); flash("Completa zona, día y hora."); return redirect(url_for("availability"))
        if day not in DAYS or turn not in TURN_ORDER:
            c.close(); flash("Completa zona, día y hora."); return redirect(url_for("availability"))
        duplicate=c.execute("SELECT 1 FROM availability WHERE active=TRUE AND zone=%s AND day=%s AND time=%s",(zone,day,time)).fetchone()
        if duplicate:
            c.close(); return redirect(url_for("availability"))
        c.execute("INSERT INTO availability(zone,day,time,turn,active) VALUES(%s,%s,%s,%s,TRUE)",(zone,day,time,turn))
        # A newly published recurring slot means this turn is no longer fully closed.
        c.execute("INSERT INTO availability_status(month,turn,is_full) VALUES(%s,%s,FALSE) ON CONFLICT(month,turn) DO UPDATE SET is_full=FALSE",(current_month,turn))
        c.commit(); c.close(); flash("Disponibilidad agregada."); return redirect(url_for("availability"))
    av=c.execute("""SELECT * FROM availability WHERE active=TRUE ORDER BY CASE day
        WHEN 'Lunes' THEN 1 WHEN 'Martes' THEN 2 WHEN 'Miércoles' THEN 3 WHEN 'Jueves' THEN 4 WHEN 'Viernes' THEN 5 WHEN 'Sábado' THEN 6 ELSE 7 END,time,zone""").fetchall()
    status_rows=c.execute("SELECT turn,is_full FROM availability_status WHERE month=%s",(current_month,)).fetchall()
    status={r["turn"]:bool(r["is_full"]) for r in status_rows}
    today=datetime.now(PERU_TZ).date(); recovery=[]; seen=set()
    for st in c.execute("SELECT id,student_name,zone FROM students WHERE status='active' ORDER BY student_name").fetchall():
        for x in automatic_recovery_slots(c, st['id'], today, 45):
            key=(x['date'],x['time'])
            if key in seen: continue
            seen.add(key)
            recovery.append({'student_name':st['student_name'],'zone':st['zone'] or 'Zona por indicar','date':x['date'],'day':x['day'],'time':x['time']})
    recovery.sort(key=lambda x:(x['date'],x['time'],x['student_name']))
    c.close()
    return render_template("availability.html",availability=av,days=DAYS,turns=TURN_ORDER,agenda_status=status,current_month=current_month,recovery_slots=recovery)


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
        c.commit(); flash("Solicitud de servicio registrada."); c.close(); return redirect(url_for("teams"))
    rows=c.execute("SELECT * FROM teams ORDER BY date,time").fetchall(); c.close(); return render_template("teams.html",teams=rows)


@app.route("/alumno/login", methods=["GET","POST"])
def parent_login():
    if request.method=="POST":
        whatsapp=normalize_whatsapp(request.form.get("whatsapp","")); password=request.form.get("password","").strip(); c=db()
        candidates=c.execute("SELECT * FROM users WHERE role='parent'").fetchall()
        u=next((x for x in candidates if normalize_whatsapp(x["whatsapp"])==whatsapp and password_matches(x["password"], password)),None)
        if u:
            if not password_is_hashed(u["password"]):
                c.execute("UPDATE users SET password=%s WHERE id=%s", (hash_password(password), u["id"])); c.commit()
            c.close(); session["role"]="parent"; session["user_id"]=u["id"]; return redirect(url_for("parent_home"))
        c.close()
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
        if not u or not password_matches(u["password"], current): c.close(); flash("La contraseña actual es incorrecta."); return redirect(url_for("change_password"))
        c.execute("UPDATE users SET password=%s WHERE id=%s",(hash_password(new),u["id"])); c.commit(); c.close(); flash("Contraseña actualizada correctamente."); return redirect(url_for("parent_home"))
    return render_template("change_password.html")


@app.route("/alumno")
def parent_home():
    if session.get("role") != "parent":
        return redirect(url_for("parent_login"))
    c=db(); user_id=session["user_id"]; today=datetime.now(PERU_TZ).date(); today_iso=today.isoformat()
    sync_payment_status(c); c.commit()
    children=c.execute("SELECT * FROM students WHERE user_id=%s AND status='active' ORDER BY student_name", (user_id,)).fetchall()
    if not children:
        c.close(); flash("No hay alumnos asociados a esta cuenta."); return redirect(url_for("logout"))
    child_data=[]
    for s in children:
        upcoming=c.execute("""SELECT * FROM classes WHERE student_id=%s AND date>=%s
            AND status IN ('scheduled','rescheduled','postponed') ORDER BY date,time LIMIT 60""", (s["id"],today_iso)).fetchall()
        history=c.execute("""SELECT * FROM classes WHERE student_id=%s AND date<%s
            AND status<>'cancelled' ORDER BY date DESC,time DESC LIMIT 100""", (s["id"],today_iso)).fetchall()
        payments_rows=c.execute("SELECT * FROM payments WHERE student_id=%s ORDER BY month DESC,id DESC", (s["id"],)).fetchall()
        current_month=today.strftime("%Y-%m")
        current_payments=[p for p in payments_rows if p["month"]==current_month and p["status"]=='paid']
        current_paid=sum(float(p["amount"] or 0) for p in current_payments)
        attended_count=c.execute("SELECT COUNT(*) AS n FROM classes WHERE student_id=%s AND status='attended' AND date LIKE %s", (s["id"],current_month+'%')).fetchone()["n"]
        month_scheduled=c.execute("SELECT COUNT(*) AS n FROM classes WHERE student_id=%s AND date LIKE %s AND status<>'cancelled'", (s["id"],current_month+'%')).fetchone()["n"]
        month_paid_classes=c.execute("SELECT COUNT(*) AS n FROM classes WHERE student_id=%s AND date LIKE %s AND payment_status='paid' AND status<>'cancelled'", (s["id"],current_month+'%')).fetchone()["n"]
        # The monthly summary includes classes already taken as well as upcoming
        # classes. The parent should see the full number scheduled for the month,
        # not only the remaining future classes.
        scheduled_count=month_scheduled
        paid_count=month_paid_classes
        pending_count=max(0, month_scheduled-month_paid_classes)
        month_payment_status = current_paid > 0 or (month_scheduled > 0 and month_paid_classes >= month_scheduled)
        extra_count=max(0, scheduled_count-paid_count)
        # Solo la siguiente clase queda habilitada para gestión directa del padre/madre.
        reprogrammable_id=upcoming[0]["id"] if upcoming else None
        child_data.append({
            "student":s, "upcoming":upcoming, "history":history, "payments":payments_rows,
            "current_paid":current_paid, "current_month":current_month, "month_scheduled":month_scheduled,
            "month_paid_classes":month_paid_classes, "month_attended":attended_count, "month_payment_status":month_payment_status,
            "scheduled_count":scheduled_count, "paid_count":paid_count, "pending_count":pending_count,
            "extra_count":extra_count, "reprogrammable_id":reprogrammable_id,
        })
    c.close(); return render_template("parent.html", children=child_data)



@app.route("/alumno/solicitar", methods=["GET","POST"])
def parent_request_class():
    if session.get("role") != "parent":
        return redirect(url_for("parent_login"))
    c=db(); user_id=session["user_id"]
    children=c.execute("SELECT * FROM students WHERE user_id=%s AND status='active' ORDER BY student_name",(user_id,)).fetchall()
    if not children:
        c.close(); flash("No hay alumnos asociados a esta cuenta."); return redirect(url_for("logout"))
    selected_id=request.args.get("student_id",type=int)
    if request.method=="POST":
        selected_id=request.form.get("student_id",type=int)
        child=c.execute("SELECT * FROM students WHERE id=%s AND user_id=%s AND status='active'",(selected_id,user_id)).fetchone()
        if not child:
            c.close(); flash("Alumno no válido."); return redirect(url_for("parent_request_class"))
        slot_value=request.form.get("slot","").strip()
        try: new_date,new_time=slot_value.split("|",1)
        except ValueError:
            c.close(); flash("Selecciona un horario."); return redirect(url_for("parent_request_class",student_id=selected_id))
        slots=current_student_slots(c,child,datetime.now(PERU_TZ).date())
        if not any(x["date"]==new_date and x["time"]==new_time for x in slots):
            c.close(); flash("Ese horario ya no está disponible."); return redirect(url_for("parent_request_class",student_id=selected_id))
        # The parent sends a request; Coach confirms/programs it. No class is created here.
        message=(f"⚽ Hola, Coach Juan Carlos.\n\nQuisiera solicitar una nueva clase para mi hijo/a.\n\n"
                 f"Alumno: {child['student_name']}\nZona: {child['zone'] or 'Por indicar'}\n"
                 f"Fecha solicitada: {display_day(new_date).lower()} {display_date(new_date)}\n"
                 f"Hora solicitada: {display_time(new_time)}\n\n"
                 "La solicitud fue realizada desde la aplicación de JC Fútbol Coach.")
        c.close()
        return render_template("rescheduled.html", current={"student_id":child["id"],"student_name":child["student_name"],"student_zone":child["zone"],"date":new_date,"time":new_time,"place":child["place"]}, old_date=None,new_date=new_date,new_time=new_time,zone=child["zone"], whatsapp_url=f"https://wa.me/{COACH_WHATSAPP}?text={quote(message)}", request_mode=True)
    if not selected_id:
        selected_id=children[0]["id"]
    selected=next((x for x in children if x["id"]==selected_id),children[0])
    slots=current_student_slots(c,selected,datetime.now(PERU_TZ).date())
    c.close()
    return render_template("parent_request.html",children=children,selected=selected,slots=slots)

@app.route("/alumno/clase/<int:class_id>/reprogramar", methods=["GET","POST"])
def reschedule(class_id):
    if session.get("role")!="parent": return redirect(url_for("parent_login"))
    c=db(); current=c.execute("""SELECT classes.*,students.student_name,students.zone AS student_zone,students.parent_name
        FROM classes JOIN students ON students.id=classes.student_id WHERE classes.id=%s AND students.user_id=%s""",(class_id,session["user_id"])).fetchone()
    if not current: c.close(); flash("Clase no encontrada."); return redirect(url_for("parent_home"))
    # La reprogramación directa se aplica a la próxima clase del alumno seleccionado,
    # no a la próxima clase global de toda la cuenta. Esto permite que una familia
    # con varios hijos gestione correctamente a cada alumno.
    next_class=c.execute("""SELECT classes.id FROM classes
        WHERE classes.student_id=%s AND classes.date>=%s
        AND classes.status IN ('scheduled','rescheduled','postponed')
        ORDER BY classes.date,classes.time,classes.id LIMIT 1""",(current["student_id"],datetime.now(PERU_TZ).date().isoformat())).fetchone()
    if not next_class or next_class["id"]!=class_id:
        c.close(); flash("Esta no es la próxima clase de este alumno. La reprogramación directa corresponde a su próxima clase."); return redirect(url_for("parent_home"))
    if current["status"] not in ("scheduled","rescheduled","postponed"):
        c.close(); flash("Esta clase no está disponible para reprogramación directa."); return redirect(url_for("parent_home"))
    try: original_dt=parse_iso_datetime(current["date"],current["time"])
    except ValueError: c.close(); flash("No se pudo validar la fecha."); return redirect(url_for("parent_home"))
    now=datetime.now(PERU_TZ)
    if current["status"] != "postponed" and now >= original_dt-timedelta(hours=2):
        c.close(); return redirect(url_for("contacto_whatsapp",mensaje="Hola, Coach Juan Carlos. Tengo una emergencia y necesito coordinar una reprogramación de clase."))
    today=now.date()
    if request.method=="POST":
        new_date=request.form.get("date","").strip(); new_time=request.form.get("time","").strip(); zone=(current["student_zone"] or "").strip()
        try: selected=date.fromisoformat(new_date); selected_day=DAYS[selected.weekday()]
        except (ValueError,IndexError): c.close(); flash("La fecha seleccionada no es válida."); return redirect(url_for("reschedule",class_id=class_id))
        if selected<today: c.close(); flash("La nueva fecha no puede ser anterior a hoy."); return redirect(url_for("reschedule",class_id=class_id))
        available_slots=reschedule_slots(c,current,today)
        allowed=any(x["date"]==new_date and x["time"]==new_time for x in available_slots)
        occupied=c.execute("SELECT 1 FROM classes WHERE date=%s AND time=%s AND status IN ('scheduled','rescheduled') AND id<>%s LIMIT 1",(new_date,new_time,class_id)).fetchone()
        if not allowed or occupied: c.close(); flash("Ese horario ya no está disponible para reprogramar."); return redirect(url_for("reschedule",class_id=class_id))
        old_date=current["date"]; old_time=current["time"]
        c.execute("UPDATE classes SET date=%s,time=%s,status='rescheduled',original_date=COALESCE(original_date,%s),original_time=COALESCE(original_time,%s) WHERE id=%s",(new_date,new_time,old_date,old_time,class_id))
        c.execute("""INSERT INTO coach_notifications(kind,class_id,student_id,title,message)
            VALUES(%s,%s,%s,%s,%s)""", (
                "reschedule", class_id, current["student_id"], "Nueva reprogramación",
                f"{current['student_name']} cambió su clase de {display_date(old_date)} · {display_time(old_time)} a {display_date(new_date)} · {display_time(new_time)} · {zone or 'Zona por indicar'}."
            ))
        c.commit(); c.close()
        message=(f"⚽ Hola, Coach Juan Carlos.\n\nSe reprogramó una clase desde la app.\n\nAlumno: {current['student_name']}\nZona: {zone}\nClase anterior: {display_date(old_date)} · {display_time(old_time)}.\nNueva fecha: {selected_day.lower()} {selected.day} de {selected.strftime('%B').lower()} de {selected.year}.\nNueva hora: {display_time(new_time)}.\n\nLa solicitud fue realizada desde la aplicación de JC Fútbol Coach.")
        return render_template("rescheduled.html",current=current,old_date=old_date,new_date=new_date,new_time=new_time,zone=zone,whatsapp_url=f"https://wa.me/{COACH_WHATSAPP}?text={quote(message)}")
    slots=reschedule_slots(c,current,today)
    c.close(); return render_template("reschedule.html",current=current,slots=slots)



@app.route("/entrenador/clase/<int:class_id>/reprogramar", methods=["GET","POST"])
@coach_required
def coach_reschedule(class_id):
    c=db()
    current=c.execute("""SELECT classes.*,students.student_name,students.zone AS student_zone,students.parent_name
        FROM classes JOIN students ON students.id=classes.student_id WHERE classes.id=%s""",(class_id,)).fetchone()
    if not current: c.close(); flash("Clase no encontrada."); return redirect(url_for("dashboard"))
    if request.method=="POST":
        new_date=request.form.get("date","").strip(); new_time=request.form.get("time","").strip()
        try: selected=date.fromisoformat(new_date); selected_day=DAYS[selected.weekday()]
        except (ValueError,IndexError): c.close(); flash("Fecha no válida."); return redirect(url_for("coach_reschedule",class_id=class_id))
        slots=reschedule_slots(c,current,datetime.now(PERU_TZ).date())
        if not any(x["date"]==new_date and x["time"]==new_time for x in slots):
            c.close(); flash("Ese horario no está disponible para reprogramar."); return redirect(url_for("coach_reschedule",class_id=class_id))
        old_date,old_time=current["date"],current["time"]
        c.execute("UPDATE classes SET date=%s,time=%s,status='rescheduled',original_date=COALESCE(original_date,%s),original_time=COALESCE(original_time,%s) WHERE id=%s",(new_date,new_time,old_date,old_time,class_id))
        c.commit(); c.close()
        return redirect(url_for("student_detail",sid=current["student_id"]))
    slots=reschedule_slots(c,current,datetime.now(PERU_TZ).date()); c.close()
    return render_template("reschedule.html",current=current,slots=slots,coach_mode=True)

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("home"))


@app.errorhandler(404)
def page_not_found(error):
    return render_template("error.html", code=404, message="No encontramos esa página.", back_url=url_for("home")), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template("error.html", code=500, message="Ocurrió un problema al abrir esta pantalla. Si acabas de guardar información, revisa primero la ficha antes de repetir el registro.", back_url=url_for("home")), 500


init_db()

if __name__ == "__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
