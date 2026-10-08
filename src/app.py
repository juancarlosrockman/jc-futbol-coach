from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, Response, abort
import os
import hashlib
import uuid
import re
from urllib.parse import quote
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from functools import wraps
from secrets import token_hex
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg
import requests
import base64
from cryptography.fernet import Fernet, InvalidToken
from psycopg.rows import dict_row

app = Flask(__name__)
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
if not SECRET_KEY:
    raise RuntimeError("Falta la variable de entorno SECRET_KEY. Configúrala en las variables o secretos del entorno antes de iniciar la app.")
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_NAME="jcfc_session",
    MAX_CONTENT_LENGTH=512 * 1024,
)
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
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None)



GOOGLE_CALENDAR_NAME = "JC Fútbol Coach – Clases"
GOOGLE_SCOPES = "https://www.googleapis.com/auth/calendar"

def _fernet():
    # Derive a stable encryption key from the app's existing secret; no extra secret required.
    digest = hashlib.sha256(app.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))

def _token_encrypt(value):
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii") if value else None

def _token_decrypt(value):
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None

def google_oauth_configured():
    return all(os.environ.get(k, "").strip() for k in ("GOOGLE_CLIENT_ID","GOOGLE_CLIENT_SECRET","GOOGLE_REDIRECT_URI"))

def _google_setting(c, coach_user_id):
    return c.execute("SELECT * FROM google_calendar_settings WHERE coach_user_id=%s", (coach_user_id,)).fetchone()

def _google_refresh_access(c, setting):
    refresh = _token_decrypt(setting.get("refresh_token"))
    if not refresh:
        raise RuntimeError("Google Calendar no está autorizado. Conecta la cuenta desde la aplicación.")
    response = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }, timeout=20)
    if response.status_code >= 400:
        raise RuntimeError("Google no pudo renovar la autorización. Desconecta y vuelve a conectar Google Calendar.")
    payload = response.json()
    expiry = datetime.now(PERU_TZ).replace(tzinfo=None) + timedelta(seconds=int(payload.get("expires_in",3600)))
    c.execute("UPDATE google_calendar_settings SET access_token=%s,token_expiry=%s,updated_at=CURRENT_TIMESTAMP WHERE coach_user_id=%s",
              (_token_encrypt(payload["access_token"]), expiry, setting["coach_user_id"]))
    setting["access_token"] = _token_encrypt(payload["access_token"])
    setting["token_expiry"] = expiry
    return payload["access_token"]

def _google_access_token(c, setting):
    expiry = setting.get("token_expiry")
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=PERU_TZ)
    if setting.get("access_token") and expiry and expiry > datetime.now(PERU_TZ) + timedelta(minutes=2):
        return _token_decrypt(setting["access_token"])
    return _google_refresh_access(c, setting)

def _google_api(c, setting, method, path, **kwargs):
    token = _google_access_token(c, setting)
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = "Bearer " + token
    response = requests.request(method, "https://www.googleapis.com/calendar/v3" + path,
                                headers=headers, timeout=25, **kwargs)
    if response.status_code == 401:
        token = _google_refresh_access(c, setting)
        headers["Authorization"] = "Bearer " + token
        response = requests.request(method, "https://www.googleapis.com/calendar/v3" + path,
                                    headers=headers, timeout=25, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError("Google Calendar respondió con error HTTP " + str(response.status_code))
    return response.json() if response.content else {}

def _ensure_google_calendar(c, setting):
    if setting.get("calendar_id"):
        return setting["calendar_id"]
    calendar = _google_api(c, setting, "POST", "/calendars", json={
        "summary": GOOGLE_CALENDAR_NAME, "timeZone": "America/Lima"
    })
    c.execute("UPDATE google_calendar_settings SET calendar_id=%s,updated_at=CURRENT_TIMESTAMP WHERE coach_user_id=%s",
              (calendar["id"], setting["coach_user_id"]))
    setting["calendar_id"] = calendar["id"]
    return calendar["id"]

def _calendar_event_payload(row):
    start_dt = parse_iso_datetime(row["date"], row["time"])
    try:
        age = int(row.get("student_age") or 6)
    except (TypeError, ValueError):
        age = 6
    duration = 30 if age <= 3 else (45 if age <= 5 else 60)
    end_dt = start_dt + timedelta(minutes=duration)
    student = row.get("student_name") or "Alumno"
    place = row.get("place") or row.get("student_zone") or "Por confirmar"
    mode = row.get("mode") or "Entrenamiento"
    return {
        "summary": f"JC Fútbol Coach – {student}",
        "description": f"Entrenamiento personalizado\nAlumno: {student}\nModalidad: {mode}\nLugar: {place}\nID de clase JCFC: {row['id']}",
        "location": place,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Lima"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Lima"},
        "extendedProperties": {"private": {"jcfc_class_id": str(row["id"])}}
    }

def _set_google_sync_state(class_id, status, error=None):
    c = db()
    try:
        if status == "synced":
            c.execute("UPDATE classes SET google_sync_status=%s,google_sync_error=NULL,google_synced_at=CURRENT_TIMESTAMP WHERE id=%s", (status, class_id))
        else:
            c.execute("UPDATE classes SET google_sync_status=%s,google_sync_error=%s WHERE id=%s", (status, error, class_id))
        c.commit()
    finally:
        c.close()


def sync_class_to_google(class_id, delete=False):
    """Synchronize one JCFC class after the database transaction has succeeded.
    Google errors never roll back the JCFC class; they are recorded so the coach
    can retry synchronization later.
    """
    c = db()
    try:
        coach = c.execute("SELECT id FROM users WHERE role='coach' ORDER BY id LIMIT 1").fetchone()
        if not coach:
            return False, "No hay entrenador configurado."
        setting = _google_setting(c, coach["id"])
        if not setting or not setting.get("refresh_token"):
            c.execute("UPDATE classes SET google_sync_status='not_connected',google_sync_error=%s WHERE id=%s", ("Google Calendar no está conectado.", class_id))
            c.commit()
            return False, "Google Calendar no está conectado."
        row = c.execute("""SELECT classes.*,students.student_name,students.age AS student_age,students.zone AS student_zone
            FROM classes LEFT JOIN students ON students.id=classes.student_id WHERE classes.id=%s""", (class_id,)).fetchone()
        if not row:
            return False, "Clase no encontrada."
        event_id = row.get("google_event_id")
        calendar_id = setting.get("calendar_id") or _ensure_google_calendar(c, setting)
        if delete or row.get("status") == "cancelled":
            if event_id:
                try:
                    _google_api(c, setting, "DELETE", f"/calendars/{quote(calendar_id,safe='')}/events/{quote(event_id,safe='')}")
                except RuntimeError as e:
                    if "HTTP 404" not in str(e) and "HTTP 410" not in str(e):
                        raise
                c.execute("UPDATE classes SET google_event_id=NULL,google_sync_status='synced',google_sync_error=NULL,google_synced_at=CURRENT_TIMESTAMP WHERE id=%s", (class_id,))
            else:
                c.execute("UPDATE classes SET google_sync_status='synced',google_sync_error=NULL,google_synced_at=CURRENT_TIMESTAMP WHERE id=%s", (class_id,))
        elif row.get("status") in ("scheduled","rescheduled","postponed"):
            payload = _calendar_event_payload(row)
            if event_id:
                try:
                    event = _google_api(c, setting, "PATCH", f"/calendars/{quote(calendar_id,safe='')}/events/{quote(event_id,safe='')}", json=payload)
                except RuntimeError as e:
                    # If the event was deleted directly in Google Calendar,
                    # recreate it instead of leaving JCFC permanently unsynced.
                    if "HTTP 404" in str(e) or "HTTP 410" in str(e):
                        c.execute("UPDATE classes SET google_event_id=NULL WHERE id=%s", (class_id,))
                        event = _google_api(c, setting, "POST", f"/calendars/{quote(calendar_id,safe='')}/events", json=payload)
                    else:
                        raise
            else:
                event = _google_api(c, setting, "POST", f"/calendars/{quote(calendar_id,safe='')}/events", json=payload)
            c.execute("UPDATE classes SET google_event_id=%s,google_sync_status='synced',google_sync_error=NULL,google_synced_at=CURRENT_TIMESTAMP WHERE id=%s", (event["id"], class_id))
        c.commit()
        return True, None
    except Exception as exc:
        c.rollback()
        message = str(exc)[:500]
        try:
            c.execute("UPDATE classes SET google_sync_status='error',google_sync_error=%s WHERE id=%s", (message, class_id))
            c.commit()
        except Exception:
            c.rollback()
        app.logger.exception("Google Calendar sync failed for class %s", class_id)
        return False, message
    finally:
        c.close()


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
    c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP")
    c.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS break_tariff DOUBLE PRECISION")
    c.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS package_price DOUBLE PRECISION")
    # Package metadata is separate from the student's habitual tariff.
    # Each student can have a different individual tariff. The package-break
    # tariff therefore follows that student's own tariff, with the known
    # legacy S/55 -> S/60 exception for Salvador/legacy students. Never use a
    # global S/80 break tariff for every student.
    c.execute("""UPDATE students SET
        break_tariff = CASE
            WHEN age >= 6 AND ABS(tariff - 55) < 0.01 THEN 60
            WHEN age >= 6 THEN tariff
            ELSE tariff
        END,
        package_price = CASE
            WHEN age >= 6 AND ABS(tariff - 55) < 0.01 THEN 440
            WHEN age >= 6 THEN 600
            ELSE NULL
        END
        WHERE break_tariff IS NULL OR package_price IS NULL""")
    # Repair the incorrect S/80 default introduced by the previous pricing
    # migration, but only where the student's own tariff is different.
    c.execute("""UPDATE students SET break_tariff = CASE
        WHEN age >= 6 AND ABS(tariff - 55) < 0.01 THEN 60
        WHEN age >= 6 THEN tariff
        ELSE tariff
    END
    WHERE age >= 6 AND ABS(COALESCE(break_tariff,0) - 80) < 0.01
      AND ABS(tariff - 80) >= 0.01""")
    c.execute("ALTER TABLE availability_status ADD COLUMN IF NOT EXISTS is_full BOOLEAN NOT NULL DEFAULT FALSE")
    c.execute("""CREATE TABLE IF NOT EXISTS login_attempts(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        ip TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        attempted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_endpoint_time ON login_attempts(ip,endpoint,attempted_at DESC)")
    c.execute("""CREATE TABLE IF NOT EXISTS parent_class_reminders_seen(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id INTEGER NOT NULL,
        class_id INTEGER NOT NULL,
        seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id,class_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS push_subscriptions(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id INTEGER NOT NULL,
        endpoint TEXT NOT NULL UNIQUE,
        subscription_json TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS google_calendar_settings(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        coach_user_id INTEGER NOT NULL UNIQUE,
        calendar_id TEXT,
        access_token TEXT,
        refresh_token TEXT,
        token_expiry TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
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
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS is_recovery BOOLEAN NOT NULL DEFAULT FALSE")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS parent_reschedule_count INTEGER NOT NULL DEFAULT 0")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS work_notes TEXT")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS recovery_source_class_id INTEGER")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS recovery_payment_id INTEGER")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS recovery_month TEXT")
    c.execute("UPDATE classes SET is_recovery=TRUE WHERE status='rescheduled' AND original_date IS NOT NULL AND is_recovery=FALSE")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS google_event_id TEXT")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS google_sync_status TEXT NOT NULL DEFAULT 'pending'")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS google_sync_error TEXT")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS google_synced_at TIMESTAMP")
    c.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS payment_type TEXT NOT NULL DEFAULT 'regular'")
    c.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS sessions_total INTEGER NOT NULL DEFAULT 1")
    c.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP")
    c.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS applied_tariff DOUBLE PRECISION")
    c.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS recovery_slot_id INTEGER")
    c.execute("""CREATE TABLE IF NOT EXISTS recovery_slots(
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        date TEXT NOT NULL, time TEXT NOT NULL, zone TEXT NOT NULL, place TEXT NOT NULL,
        active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_recovery_slots_date_time ON recovery_slots(date,time,active)")
    c.execute("ALTER TABLE availability ADD COLUMN IF NOT EXISTS turn TEXT NOT NULL DEFAULT 'Tarde / noche'")
    c.execute("UPDATE availability SET turn=CASE WHEN CAST(SPLIT_PART(time, ':', 1) AS INTEGER) < 12 THEN 'Mañana' ELSE 'Tarde / noche' END")
    c.execute("CREATE INDEX IF NOT EXISTS idx_classes_student_date ON classes(student_id,date,time)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_classes_date_time ON classes(date,time,status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_payments_student_month ON payments(student_id,month,status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_role_whatsapp ON users(role,whatsapp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_availability_zone_turn ON availability(zone,turn,active)")
    refresh_all_active_period_pricing(c)
    merge_duplicate_parent_accounts(c)
    migrate_plaintext_passwords(c)

    # Repair existing database records automatically. This is deliberately
    # executed at startup so already-created shared classes (such as a student
    # who was added to a class before the original class was cancelled) do not
    # require the coach to add the student again.
    repair_all_existing_recoveries(c)
    sync_payment_status(c)
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


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = token_hex(32)
        session["csrf_token"] = token
    return token


def parent_presence_label(last_seen):
    if not last_seen:
        return "Sin conexión registrada"
    if last_seen.tzinfo is not None:
        last_seen = last_seen.astimezone(PERU_TZ).replace(tzinfo=None)
    now = datetime.now(PERU_TZ).replace(tzinfo=None)
    seconds = max(0, int((now-last_seen).total_seconds()))
    if seconds <= 300:
        return "🟢 En línea ahora"
    if seconds < 60*60:
        return f"Última conexión: hace {max(1, seconds//60)} min"
    if seconds < 24*60*60:
        return f"Última conexión: hace {seconds//3600} h"
    return "Última conexión: " + last_seen.strftime("%d/%m/%Y %H:%M")


@app.context_processor
def inject_security_helpers():
    return {"csrf_token": csrf_token}


@app.before_request
def track_authenticated_parent_presence():
    # A recent request means the parent is using the app. Five minutes is the
    # online window; the timestamp itself is retained as the last connection.
    if session.get("role") == "parent" and session.get("user_id") and request.endpoint != "static":
        try:
            c = db()
            c.execute("UPDATE users SET last_seen_at=CURRENT_TIMESTAMP WHERE id=%s AND role='parent'", (session["user_id"],))
            c.commit(); c.close()
        except Exception:
            pass


@app.before_request
def protect_state_changing_requests():
    if request.method == "POST":
        expected = session.get("csrf_token")
        provided = request.form.get("csrf_token", "")
        if not expected or not provided or not secrets_compare(expected, provided):
            abort(400, description="Solicitud no válida. Recarga la página e inténtalo nuevamente.")


def secrets_compare(a, b):
    import hmac
    return hmac.compare_digest(str(a), str(b))


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if request.is_secure or request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; "
        "img-src 'self' data:; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "manifest-src 'self'; "
        "worker-src 'self'"
    )
    if session.get("role") in ("coach", "parent"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


LOGIN_MAX_FAILURES = 8
LOGIN_WINDOW_MINUTES = 15

def client_ip():
    # Do not trust user-supplied forwarding headers for the security limit.
    return request.remote_addr or "unknown"


def login_is_rate_limited(endpoint):
    c = db()
    c.execute("DELETE FROM login_attempts WHERE attempted_at < CURRENT_TIMESTAMP - INTERVAL '1 hour'")
    row = c.execute(
        "SELECT COUNT(*) AS n FROM login_attempts WHERE ip=%s AND endpoint=%s AND attempted_at >= CURRENT_TIMESTAMP - INTERVAL '15 minutes'",
        (client_ip(), endpoint)
    ).fetchone()
    c.commit(); c.close()
    return int(row["n"] or 0) >= LOGIN_MAX_FAILURES


def record_login_failure(endpoint):
    c = db()
    c.execute("INSERT INTO login_attempts(ip,endpoint) VALUES(%s,%s)", (client_ip(), endpoint))
    c.commit(); c.close()


def clear_login_failures(endpoint):
    c = db()
    c.execute("DELETE FROM login_attempts WHERE ip=%s AND endpoint=%s", (client_ip(), endpoint))
    c.commit(); c.close()


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


def get_package_terms(student_row):
    age=int(student_row.get("age") or 0); tariff=float(student_row.get("tariff") or 0)
    br=float(student_row.get("break_tariff") or 0); pp=student_row.get("package_price")
    pp=float(pp) if pp is not None else 0.0
    if age >= 6:
        if abs(tariff-55) < 0.01:
            return 440.0, br or 60.0
        return pp or 600.0, br or 80.0
    return 0.0, br or tariff

def refresh_period_pricing(c, student_id, month):
    student=c.execute("SELECT * FROM students WHERE id=%s",(student_id,)).fetchone()
    if not student: return
    package_price, break_tariff=get_package_terms(student)
    rows=c.execute("""SELECT id FROM classes WHERE student_id=%s AND date LIKE %s
        AND status IN ('scheduled','rescheduled','postponed') AND is_recovery=FALSE
        AND payment_status<>'paid' ORDER BY date,time,id""",(student_id,month+'%')).fetchall()
    if not rows: return
    if package_price > 0 and len(rows) >= 8:
        unit=package_price/8.0
        amounts=[unit if i<8 else break_tariff for i in range(len(rows))]
    else:
        amounts=[break_tariff]*len(rows)
    with c.connection.cursor() as cur:
        cur.executemany("UPDATE classes SET amount=%s WHERE id=%s",[(amounts[i],rows[i]["id"]) for i in range(len(rows))])

def refresh_all_active_period_pricing(c, student_id=None):
    students=c.execute("SELECT id FROM students WHERE status='active'"+(" AND id=%s" if student_id else ""),((student_id,) if student_id else ())).fetchall()
    for st in students:
        months=c.execute("""SELECT DISTINCT LEFT(date,7) AS month FROM classes
            WHERE student_id=%s AND status IN ('scheduled','rescheduled','postponed')
              AND is_recovery=FALSE AND payment_status<>'paid'""",(st["id"],)).fetchall()
        for m in months:
            if m["month"]: refresh_period_pricing(c,st["id"],m["month"])

def sync_payment_status(c, student_id=None):
    """Link scheduled classes to the correct monthly payment.

    Normal classes are covered only by the payment for their calendar month.
    Recovery classes use the month/payment of their original class, so a
    September recovery scheduled in October never consumes October's payment.
    """
    students=c.execute("SELECT id,tariff FROM students WHERE status='active'" + (" AND id=%s" if student_id else ""), ((student_id,) if student_id else ())).fetchall()
    for st in students:
        sid=st["id"]; tariff=float(st["tariff"] or 0)
        c.execute("UPDATE classes SET payment_status='paid' WHERE student_id=%s AND payment_id IS NOT NULL", (sid,))
        payments=c.execute("SELECT id,month,amount,sessions_total,payment_type FROM payments WHERE student_id=%s AND status='paid' ORDER BY id", (sid,)).fetchall()
        capacity={}
        for p in payments:
            if p["payment_type"]=='regular':
                cap=int(p["sessions_total"] or ((float(p["amount"] or 0)//tariff) if tariff>0 else 0))
            else:
                cap=int(p["sessions_total"] or 8)
            used=c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s AND status<>'cancelled'", (p["id"],)).fetchone()["n"]
            capacity[p["id"]]={"payment":p,"remaining":max(0,cap-used)}

        pending=c.execute("""SELECT id,date,original_date,is_recovery FROM classes
            WHERE student_id=%s AND payment_id IS NULL AND status IN ('scheduled','rescheduled','postponed')
            ORDER BY date,time,id""", (sid,)).fetchall()
        for cl in pending:
            payment_month=(cl["original_date"][:7] if cl.get("is_recovery") and cl.get("original_date") else cl["date"][:7])
            candidates=[]
            for info in capacity.values():
                p=info["payment"]
                if info["remaining"]<=0 or p["payment_type"]!='regular' or p["month"]!=payment_month:
                    continue
                candidates.append(info)
            if not candidates and not cl.get("is_recovery"):
                for info in capacity.values():
                    p=info["payment"]
                    if info["remaining"]>0 and p["payment_type"]=='package_8': candidates.append(info)
            if candidates:
                info=candidates[0]; pid=info["payment"]["id"]
                c.execute("UPDATE classes SET payment_id=%s,payment_status='paid' WHERE id=%s", (pid,cl["id"]))
                info["remaining"]-=1
            else:
                c.execute("UPDATE classes SET payment_status='pending' WHERE id=%s", (cl["id"],))


def attach_recovery_payment(c, class_id, source_class_id=None):
    """Attach a future class to the payment of its cancelled source class.

    A recovery is never a new debt. This helper is intentionally tolerant of
    the real coach workflow: the new shared class may be created first and the
    old class cancelled afterwards, or the old class may already be cancelled.
    """
    row=c.execute("SELECT * FROM classes WHERE id=%s", (class_id,)).fetchone()
    if not row or row.get("payment_id") is not None:
        return False

    target_date=date.fromisoformat(row["date"])
    candidates=[]
    if source_class_id:
        src=c.execute("SELECT * FROM classes WHERE id=%s AND student_id=%s AND status='cancelled'", (source_class_id,row["student_id"])).fetchone()
        if src: candidates=[src]
    if not candidates:
        prev_month=(target_date.replace(day=1)-timedelta(days=1)).strftime('%Y-%m')
        # Prefer a cancelled class from the immediately preceding month that
        # matches the destination's time/place. This is the common manual
        # workflow when a student is added to a shared class first.
        candidates=c.execute("""SELECT * FROM classes WHERE student_id=%s AND status='cancelled'
            AND date LIKE %s AND recovery_source_class_id IS NULL
            AND (time=%s OR place=%s)
            ORDER BY CASE WHEN time=%s AND place=%s THEN 0 ELSE 1 END, date DESC,id DESC""",
            (row["student_id"],prev_month+'%',row.get("time"),row.get("place"),row.get("time"),row.get("place"))).fetchall()
        if not candidates:
            candidates=c.execute("""SELECT * FROM classes WHERE student_id=%s AND status='cancelled'
                AND date LIKE %s AND recovery_source_class_id IS NULL
                ORDER BY date DESC,id DESC""", (row["student_id"],prev_month+'%')).fetchall()

    student_row=c.execute("SELECT tariff FROM students WHERE id=%s", (row["student_id"],)).fetchone()
    tariff=float(student_row["tariff"] or 0) if student_row else 0

    for src in candidates:
        src_month=src["date"][:7]
        # First choice: the payment that was attached to the original class.
        payment_ids=[]
        if src.get("recovery_payment_id"):
            payment_ids.append(src["recovery_payment_id"])
        if src.get("payment_id") and src["payment_id"] not in payment_ids:
            payment_ids.append(src["payment_id"])

        # Fallback for legacy rows where cancellation already removed the
        # payment_id: use a paid payment from the original month. Support both
        # normal monthly payments and legacy 8-class package records.
        payments=c.execute("""SELECT * FROM payments WHERE student_id=%s AND month=%s AND status='paid'
            AND payment_type IN ('regular','package_8') ORDER BY id DESC""", (row["student_id"],src_month)).fetchall()
        payment_ids.extend([p["id"] for p in payments if p["id"] not in payment_ids])
        # Legacy/manual records can have a payment month stored differently
        # from the class date. If the cancelled source was already paid, use
        # the most recent paid monthly payment before the recovery date as a
        # final fallback, never the new month's payment.
        if not payment_ids:
            fallback=c.execute("""SELECT * FROM payments WHERE student_id=%s AND status='paid'
                AND payment_type IN ('regular','package_8') AND month < %s
                ORDER BY month DESC,id DESC LIMIT 3""", (row["student_id"],row["date"][:7])).fetchall()
            payment_ids.extend([p["id"] for p in fallback if p["id"] not in payment_ids])

        for pid in payment_ids:
            p=c.execute("SELECT * FROM payments WHERE id=%s AND student_id=%s AND status='paid'", (pid,row["student_id"])).fetchone()
            if not p:
                continue
            if p["payment_type"]=='regular':
                cap=int(p["sessions_total"] or ((float(p["amount"] or 0)//tariff) if tariff>0 else 0))
            else:
                cap=int(p["sessions_total"] or 8)
            used=c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s AND status<>'cancelled'",(p["id"],)).fetchone()["n"]
            # A cancelled source itself is not part of used, so its original
            # payment capacity becomes available for the recovery.
            if used >= cap:
                # Legacy repair: if the source class was cancelled after the
                # payment was recorded but before it was linked, the payment
                # capacity may reflect only the classes that were still active.
                # The cancelled source itself is the missing paid class. For a
                # monthly payment, expand the recorded session count by one so
                # the original payment can cover its recovery rather than
                # creating a false new debt.
                if p["payment_type"] == 'regular' and src.get("recovery_source_class_id") is None:
                    cap = used + 1
                    c.execute("UPDATE payments SET sessions_total=GREATEST(COALESCE(sessions_total,0),%s) WHERE id=%s", (cap,p["id"]))
                else:
                    continue
            c.execute("""UPDATE classes SET payment_id=%s,payment_status='paid',is_recovery=TRUE,
                original_date=%s,original_time=%s,recovery_source_class_id=%s,recovery_month=%s,
                notes=CASE WHEN COALESCE(notes,'')='' THEN 'Recuperación de un período anterior' ELSE notes END
                WHERE id=%s""",(p["id"],src["date"],src["time"],src["id"],src_month,class_id))
            c.execute("""UPDATE classes SET recovery_payment_id=%s,
                notes=CASE WHEN COALESCE(notes,'')='' THEN %s ELSE notes END
                WHERE id=%s""",(p["id"],f"Recuperada en clase {class_id}",src["id"]))
            return True
    return False


def repair_pending_recoveries(c, student_id=None):
    """Repair future classes that replace a cancelled class from the prior month.

    This covers both the normal reprogramming flow and the manual workflow in
    which the coach first adds the student to a Clase compartida and later
    cancels the original class.
    """
    query="""SELECT id FROM classes WHERE student_id=%s
        AND (payment_id IS NULL OR payment_status='pending')
        AND status IN ('scheduled','rescheduled','postponed')
        ORDER BY date,time,id"""
    rows=c.execute(query,(student_id,)).fetchall() if student_id is not None else []
    repaired=0
    for r in rows:
        if attach_recovery_payment(c,r["id"]): repaired+=1
    return repaired


def repair_all_existing_recoveries(c):
    """Repair already-created future classes without requiring another user action.

    This is intentionally run during startup so a class that is already in the
    database (for example, an existing student already added to a shared class)
    can be converted into the recovery of a cancelled, already-paid class.
    It only considers pending future classes and lets attach_recovery_payment
    enforce the prior-month/source-payment rules.
    """
    rows=c.execute("""SELECT DISTINCT student_id FROM classes
        WHERE (payment_id IS NULL OR payment_status='pending')
        AND status IN ('scheduled','rescheduled','postponed')
        AND date >= %s""", (datetime.now(PERU_TZ).date().isoformat(),)).fetchall()
    repaired=0
    for r in rows:
        repaired += repair_pending_recoveries(c, r["student_id"])
    return repaired


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
    horizon = today + timedelta(days=90)
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
    for x in c.execute("SELECT id,date,time,zone,place FROM recovery_slots WHERE active=TRUE AND date>=%s AND zone=%s ORDER BY date,time,id", (today.isoformat(), zone)).fetchall():
        key=(x["date"],x["time"])
        if key in seen or (x["date"]==current["date"] and x["time"]==current["time"]): continue
        occupied=c.execute("SELECT 1 FROM classes WHERE date=%s AND time=%s AND status IN ('scheduled','rescheduled','postponed') AND id<>%s LIMIT 1",(x["date"],x["time"],current["id"])).fetchone()
        if not occupied:
            seen.add(key); slots.append({"date":x["date"],"day":DAYS[date.fromisoformat(x["date"]).weekday()],"time":x["time"],"zone":x["zone"],"place":x["place"],"kind":"manual_recovery","recovery_slot_id":x["id"]})
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
        "parent_presence_label": parent_presence_label,
    }


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")


@app.route("/")
def home():
    # Keep an authenticated user inside the correct private area when the
    # browser/PWA is reopened without explicitly logging out.
    if session.get("role") == "coach":
        return redirect(url_for("dashboard"))
    if session.get("role") == "parent":
        return redirect(url_for("parent_home"))
    return render_template("home.html")


@app.route("/nuevo", methods=["GET", "POST"])
def nuevo():
    c = db()
    if request.method == "POST":
        parent_name = request.form.get("parent_name", "").strip()
        registrant_type = request.form.get("registrant_type", "parent").strip()
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
        if zone == "__coordinar__":
            zone = "A coordinar con el Coach"
        if turn not in TURN_ORDER:
            c.close(); flash("Completa los datos obligatorios."); return redirect(url_for("nuevo"))
        current_month=datetime.now(PERU_TZ).strftime("%Y-%m")
        closed=c.execute("SELECT is_full FROM availability_status WHERE month=%s AND turn=%s",(current_month,turn)).fetchone()
        if closed and closed["is_full"]:
            c.close(); flash("La agenda de ese turno está llena. Puedes unirte a la lista de espera."); return redirect(url_for("nuevo"))
        schedule_day, schedule_time = "", ""
        if schedule == "__coordinar__|__coordinar__":
            schedule_day, schedule_time = "A coordinar", "Con el Coach"
        elif schedule:
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
        registrant_label = "Alumno" if registrant_type == "student" else "Padre/madre o apoderado"
        if registrant_type == "student":
            student_name = parent_name
        message = (
            "⚽ Hola, Coach Juan Carlos. Quiero solicitar un entrenamiento de fútbol.\n\n"
            f"{registrant_label}: {parent_name}\nWhatsApp: {whatsapp}\nAlumno: {student_name}\nEdad: {age}\n"
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
        if login_is_rate_limited("coach"):
            flash("Demasiados intentos. Espera unos minutos y vuelve a intentarlo.")
            return render_template("coach_login.html")
        c = db(); user = request.form.get("user", "").strip(); password = request.form.get("password", "").strip()
        u = c.execute("SELECT * FROM users WHERE role='coach' AND name=%s", (user,)).fetchone()
        valid = password_matches(u["password"], password) if u else False
        if valid:
            if not password_is_hashed(u["password"]):
                c.execute("UPDATE users SET password=%s WHERE id=%s", (hash_password(password), u["id"])); c.commit()
            c.close(); clear_login_failures("coach")
            session.clear(); session["role"] = "coach"; session["user_id"] = u["id"]; csrf_token()
            return redirect(url_for("dashboard"))
        c.close(); record_login_failure("coach")
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
    # Para cada clase de hoy, mostrar la última anotación de trabajo realizada
    # de ese mismo alumno. Esto sirve como referencia antes de iniciar la clase.
    today_with_last_work = []
    for x in today_classes:
        last_work = c.execute("""SELECT date,time,work_notes FROM classes
            WHERE student_id=%s AND status='attended' AND work_notes IS NOT NULL
              AND BTRIM(work_notes)<>'' AND date < %s
            ORDER BY date DESC,time DESC,id DESC LIMIT 1""", (x["student_id"], today)).fetchone()
        item = dict(x)
        item["last_work"] = last_work
        today_with_last_work.append(item)
    today_classes = today_with_last_work
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
    current_month = datetime.now(PERU_TZ).strftime("%Y-%m")
    month_income = c.execute("SELECT COALESCE(SUM(amount),0) AS total FROM payments WHERE status='paid' AND month=%s", (current_month,)).fetchone()["total"]
    month_pending = c.execute("""SELECT COALESCE(SUM(amount),0) AS total FROM classes
        WHERE date LIKE %s AND status IN ('scheduled','rescheduled','postponed')
        AND payment_status <> 'paid'""", (current_month + '%',)).fetchone()["total"]
    month_recoveries = c.execute("""SELECT COUNT(*) AS n FROM classes
        WHERE date LIKE %s AND is_recovery=TRUE AND status IN ('scheduled','rescheduled','postponed')""", (current_month + '%',)).fetchone()["n"]
    teams = c.execute("SELECT * FROM teams WHERE date >= %s ORDER BY date,time LIMIT 10", (today,)).fetchall()
    c.close()
    return render_template("dashboard.html", students=students, classes=classes, today_classes=today_classes,
                           wait_count=wait_count, pending_payments=pending_payments, teams=teams, notifications=notifications,
                           current_month=current_month, month_income=float(month_income or 0),
                           month_pending=float(month_pending or 0), month_recoveries=int(month_recoveries or 0))


@app.route("/entrenador/alumnos")
@coach_required
def students():
    c = db(); rows = c.execute("""SELECT students.*, users.last_seen_at AS parent_last_seen
        FROM students LEFT JOIN users ON users.id=students.user_id
        ORDER BY students.status DESC,students.student_name""").fetchall(); c.close()
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
            package_price = 440.0 if age >= 6 and abs(tariff-55.0) < 0.01 else (600.0 if age >= 6 else None)
            break_tariff = 60.0 if age >= 6 and abs(tariff-55.0) < 0.01 else (80.0 if age >= 6 else tariff)
            sid = c.execute("""INSERT INTO students(user_id,parent_name,parent_whatsapp,student_name,dni,age,zone,mode,place,tariff,break_tariff,package_price,photo_consent)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (user_id,parent_name,parent_whatsapp,student_name,dni,age,zone,mode,place,tariff,break_tariff,package_price,photo_consent)).fetchone()["id"]
            created = save_class_groups(c, sid, tariff, request.form)
            payment_id = save_optional_payment(c, sid, request.form, created)
            c.commit()
            created_for_sync = list(created)
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
    # The coach may reserve classes before receiving payment.  In that case
    # the class itself must remember the tariff expected for that period so
    # Payments can later calculate the correct amount without changing the
    # student's normal tariff.
    try:
        applied_tariff=float(form.get("period_tariff") or tariff)
    except (TypeError,ValueError):
        applied_tariff=float(tariff or 0)
    if applied_tariff <= 0:
        raise ValueError("invalid_class")
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
            VALUES(%s,%s,%s,%s,%s,%s,'scheduled','pending',%s,%s,%s,%s) RETURNING id""",(sid,d,t,mode,place,applied_tariff,d,t,notes,session_group_id)).fetchone()
        created.append(row["id"])
    for month in sorted({form.get(f"class_date_{idx}","").strip()[:7] for idx in indexes if form.get(f"class_date_{idx}","").strip()}):
        refresh_period_pricing(c,sid,month)
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
        try: applied_tariff=float(form.get("payment_tariff") or 0)
        except (TypeError,ValueError): applied_tariff=0
        # When classes were already priced by the period rule, use that
        # pricing if the received amount exactly matches the scheduled classes.
        if created_ids:
            class_rows=c.execute("SELECT id,amount,date FROM classes WHERE id=ANY(%s)",(created_ids,)).fetchall()
            rates={round(float(r["amount"] or 0),2) for r in class_rows if float(r["amount"] or 0)>0}
            class_total=sum(float(r["amount"] or 0) for r in class_rows if (r["date"] or "").startswith(month))
            if len(rates)==1 and class_total>0 and abs(amount-class_total)<0.01:
                applied_tariff=next(iter(rates))
        if applied_tariff<=0: applied_tariff=tariff
        total = int(amount // applied_tariff) if applied_tariff > 0 else 0
        if total < 1:
            raise ValueError("payment_amount")
    pid = c.execute("""INSERT INTO payments(student_id,month,amount,method,status,note,payment_type,sessions_total,paid_at,applied_tariff)
        VALUES(%s,%s,%s,%s,'paid',%s,%s,%s,%s,%s) RETURNING id""",
        (sid, month, amount, method, form.get("payment_note", ""), ptype, total, datetime.now(PERU_TZ), applied_tariff if ptype=='regular' else 75.0)).fetchone()["id"]
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
    c = db(); s = c.execute("""SELECT students.*, users.last_seen_at AS parent_last_seen
        FROM students LEFT JOIN users ON users.id=students.user_id WHERE students.id=%s""", (sid,)).fetchone()
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
        try:
            raw_break_tariff=request.form.get("break_tariff")
            break_tariff=float(raw_break_tariff) if raw_break_tariff not in (None, "") else (60.0 if age >= 6 and abs(tariff-55.0) < 0.01 else tariff)
        except (ValueError,TypeError):
            break_tariff=60.0 if age >= 6 and abs(tariff-55.0) < 0.01 else tariff
        if break_tariff <= 0:
            break_tariff=60.0 if age >= 6 and abs(tariff-55.0) < 0.01 else tariff
        package_price = 440.0 if age >= 6 and abs(tariff-55.0) < 0.01 else (600.0 if age >= 6 else None)
        c.execute("""UPDATE students SET user_id=%s,parent_name=%s,parent_whatsapp=%s,student_name=%s,dni=%s,age=%s,zone=%s,mode=%s,place=%s,tariff=%s,break_tariff=%s,package_price=%s,photo_consent=%s WHERE id=%s""",
                  (user_id,parent_name,parent_whatsapp,student_name,dni,age,request.form["zone"].strip(),request.form["mode"],request.form["place"].strip(),tariff,break_tariff,package_price,request.form["photo_consent"],sid))
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
    repair_pending_recoveries(c, sid)
    sync_payment_status(c, sid)
    c.commit(); s = c.execute("""SELECT students.*, users.last_seen_at AS parent_last_seen
        FROM students LEFT JOIN users ON users.id=students.user_id WHERE students.id=%s""", (sid,)).fetchone()
    if not s:
        c.close(); flash("Alumno no encontrado."); return redirect(url_for("students"))
    classes = c.execute("SELECT * FROM classes WHERE student_id=%s ORDER BY date DESC,time DESC,id DESC", (sid,)).fetchall()
    payments = c.execute("SELECT * FROM payments WHERE student_id=%s ORDER BY month DESC,id DESC", (sid,)).fetchall()
    last_work = c.execute("SELECT date,time,work_notes FROM classes WHERE student_id=%s AND status='attended' AND work_notes IS NOT NULL AND BTRIM(work_notes)<>'' ORDER BY date DESC,time DESC,id DESC LIMIT 1", (sid,)).fetchone()
    current_month=datetime.now(PERU_TZ).strftime("%Y-%m")
    active_classes=[x for x in classes if x["status"]!='cancelled']
    period_classes=[]
    for x in active_classes:
        period=(x.get("recovery_month") or (x.get("original_date")[:7] if x.get("is_recovery") and x.get("original_date") else None) or x["date"][:7])
        if period==current_month:
            period_classes.append(x)
    summary={
        "scheduled": len(period_classes),
        "paid": sum(1 for x in period_classes if x["payment_status"]=='paid'),
        "attended": sum(1 for x in active_classes if x["status"]=='attended' and x["date"].startswith(current_month)),
        "pending": sum(1 for x in period_classes if x["payment_status"]!='paid'),
    }
    current_paid = sum(float(p["amount"] or 0) for p in payments if p["status"]=='paid' and p["month"]==current_month)
    unpaid_period = [x for x in period_classes if x["payment_status"]!='paid']
    package_price, break_tariff = get_package_terms(s)
    current_pending = sum(float(x["amount"] or 0) for x in unpaid_period)
    current_recoveries = sum(1 for x in period_classes if x.get("is_recovery") and x["status"] in ('scheduled','rescheduled','postponed'))
    today_iso=datetime.now(PERU_TZ).date().isoformat()
    # "Próximas" means every future class, regardless of payment status.
    # "Este mes" is the complete current-month agenda, not just paid classes.
    upcoming=sorted([x for x in active_classes if x["date"]>=today_iso and x["status"] in ('scheduled','rescheduled','postponed')], key=lambda x:(x["date"],x["time"],x["id"]))
    history=sorted([x for x in active_classes if x["date"]<today_iso], key=lambda x:(x["date"],x["time"],x["id"]), reverse=True)
    packages = []
    for p in payments:
        if p["payment_type"] == "package_8":
            assigned = c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s", (p["id"],)).fetchone()["n"]
            attended = c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s AND status='attended'", (p["id"],)).fetchone()["n"]
            packages.append({"payment": p, "assigned": assigned, "attended": attended, "remaining": max(0, (p["sessions_total"] or 8) - assigned)})
    c.close(); return render_template("student_detail.html", s=s, classes=classes, period_classes=period_classes, upcoming=upcoming, history=history, current_month=current_month, payments=payments, packages=packages, summary=summary, last_work=last_work, current_paid=current_paid, current_pending=current_pending, current_recoveries=current_recoveries)


@app.route("/entrenador/clase/<int:class_id>/compartir", methods=["POST"])
@coach_required
def add_student_to_shared_class(class_id):
    c=db()
    current=c.execute("SELECT * FROM classes WHERE id=%s",(class_id,)).fetchone()
    if not current:
        c.close(); flash("Clase no encontrada."); return redirect(url_for("dashboard"))
    try: sid=int(request.form.get("student_id",""))
    except (TypeError,ValueError):
        c.close(); flash("Selecciona un alumno válido."); return redirect(url_for("edit_class",class_id=class_id))
    student=c.execute("SELECT * FROM students WHERE id=%s AND status='active'",(sid,)).fetchone()
    if not student:
        c.close(); flash("Alumno no encontrado."); return redirect(url_for("edit_class",class_id=class_id))
    if sid==current["student_id"]:
        c.close(); flash("Ese alumno ya pertenece a la clase."); return redirect(url_for("edit_class",class_id=class_id))
    duplicate=c.execute("SELECT * FROM classes WHERE student_id=%s AND date=%s AND time=%s AND status<>'cancelled' ORDER BY id LIMIT 1",(sid,current["date"],current["time"])).fetchone()
    if duplicate:
        # IMPORTANT: the student is already scheduled at this exact time.
        # Do not create a second class. Reuse the existing class and turn the
        # two records into one Clase compartida. This is the real workflow for
        # a student such as Marianito who was already added manually.
        group=current.get("session_group_id") or duplicate.get("session_group_id") or str(uuid.uuid4())
        if current.get("session_group_id") and duplicate.get("session_group_id") and current.get("session_group_id") != duplicate.get("session_group_id"):
            # Merge both existing groups into the current shared class.
            c.execute("UPDATE classes SET session_group_id=%s WHERE session_group_id=%s",(group,duplicate["session_group_id"]))
        else:
            c.execute("UPDATE classes SET session_group_id=%s WHERE id IN (%s,%s)" % ("%s", "%s", "%s"),(group,current["id"],duplicate["id"]))
        # If the existing class is a recovery created manually, attach it to
        # the original paid class instead of making it a new unpaid class.
        attach_recovery_payment(c,duplicate["id"])
        repair_pending_recoveries(c,sid)
        sync_payment_status(c,sid)
        c.commit(); c.close(); sync_class_to_google(duplicate["id"])
        flash(f"{student['student_name']} ya tenía una clase en ese horario y fue incorporado a la Clase compartida.")
        return redirect(url_for("edit_class",class_id=class_id))
    group=current.get("session_group_id") or str(uuid.uuid4())
    c.execute("UPDATE classes SET session_group_id=%s WHERE id=%s",(group,class_id))
    row=c.execute("""INSERT INTO classes(student_id,date,time,mode,place,amount,status,payment_status,original_date,original_time,notes,session_group_id)
        VALUES(%s,%s,%s,%s,%s,%s,'scheduled','pending',%s,%s,%s,%s) RETURNING id""",
        (sid,current["date"],current["time"],student["mode"],student["place"],student["tariff"],current["date"],current["time"],"Clase compartida",group)).fetchone()
    attach_recovery_payment(c,row["id"])
    repair_pending_recoveries(c,sid)
    sync_payment_status(c,sid)
    c.commit(); c.close(); sync_class_to_google(row["id"])
    flash(f"{student['student_name']} fue agregado a la clase compartida.")
    return redirect(url_for("edit_class",class_id=class_id))

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
            sync_class_to_google(class_id)
        if old_date != new_date or old_time != new_time:
            flash(f"Clase corregida: {current['student_name']} · {display_date(new_date)} · {display_time(new_time)}.")
        else:
            flash("Clase guardada sin cambios de horario.")
        return redirect(request.form.get("return_to") or url_for("student_detail", sid=current["student_id"]))
    students=c.execute("SELECT id,student_name,age FROM students WHERE status='active' AND id<>%s ORDER BY student_name",(current["student_id"],)).fetchall()
    shared_members=c.execute("SELECT classes.id,students.student_name,students.age FROM classes JOIN students ON students.id=classes.student_id WHERE classes.session_group_id=%s AND classes.status<>'cancelled' ORDER BY students.student_name",(current.get("session_group_id"),)).fetchall() if current.get("session_group_id") else []
    c.close()
    return render_template("edit_class.html", current=current, students=students, shared_members=shared_members, return_to=request.args.get("return_to", ""))


@app.route("/entrenador/clase/<int:class_id>/estado", methods=["POST"])
@coach_required
def update_class_status(class_id):
    status=request.form.get("status","").strip()
    allowed={"scheduled","attended","postponed","rescheduled","cancelled"}
    if status not in allowed: flash("Estado no válido."); return redirect(request.referrer or url_for("dashboard"))
    c=db(); row=c.execute("SELECT * FROM classes WHERE id=%s",(class_id,)).fetchone()
    if not row: c.close(); flash("Clase no encontrada."); return redirect(url_for("dashboard"))
    if status=="cancelled":
        # Keep the payment relationship available for a later recovery. A
        # cancelled class does not count as consumed by sync_payment_status.
        c.execute("UPDATE classes SET status=%s,recovery_payment_id=COALESCE(recovery_payment_id,payment_id),payment_status=CASE WHEN payment_id IS NOT NULL THEN 'paid' ELSE 'pending' END WHERE id=%s",(status,class_id))
        repair_pending_recoveries(c, row["student_id"])
        sync_payment_status(c, row["student_id"])
    else:
        c.execute("UPDATE classes SET status=%s WHERE id=%s",(status,class_id))
        if status=="attended" and row["payment_id"]: c.execute("UPDATE classes SET payment_status='paid' WHERE id=%s",(class_id,))
        sync_payment_status(c, row["student_id"])
    c.commit(); c.close()
    if status == "cancelled":
        sync_class_to_google(class_id, delete=True)
    elif status in ("scheduled","rescheduled","postponed"):
        sync_class_to_google(class_id)
    flash("Clase anulada." if status=="cancelled" else "Estado actualizado."); return redirect(request.referrer or url_for("dashboard"))


@app.route("/entrenador/clase/<int:class_id>/trabajo", methods=["POST"])
@coach_required
def save_class_work(class_id):
    work_notes = request.form.get("work_notes", "").strip()
    if len(work_notes) > 1000:
        flash("La anotación es demasiado larga. Puedes usar hasta 1000 caracteres.")
        return redirect(request.referrer or url_for("dashboard"))
    c = db()
    row = c.execute("SELECT id, student_id FROM classes WHERE id=%s", (class_id,)).fetchone()
    if not row:
        c.close(); flash("Clase no encontrada."); return redirect(url_for("dashboard"))
    c.execute("UPDATE classes SET work_notes=%s WHERE id=%s", (work_notes or None, class_id))
    c.commit(); c.close()
    flash("Trabajo de la clase guardado.")
    return redirect(request.referrer or url_for("student_detail", sid=row["student_id"]))


@app.route("/entrenador/clase/<int:class_id>/recuperacion", methods=["GET", "POST"])
@coach_required
def manual_recovery(class_id):
    c=db()
    row=c.execute("""SELECT classes.*, students.student_name, students.tariff
        FROM classes JOIN students ON students.id=classes.student_id WHERE classes.id=%s""",(class_id,)).fetchone()
    if not row:
        c.close(); flash("Clase no encontrada."); return redirect(url_for("dashboard"))
    if row["status"] == "cancelled":
        c.close(); flash("Una clase anulada no puede marcarse como recuperación. Usa una clase futura."); return redirect(url_for("student_detail",sid=row["student_id"]))
    if row["payment_status"] == "paid" and row.get("is_recovery"):
        c.close(); flash("Esta clase ya está registrada como recuperación pagada."); return redirect(url_for("student_detail",sid=row["student_id"]))
    current_month=row["date"][:7]
    payments=c.execute("""SELECT p.*,
        (SELECT COUNT(*) FROM classes cc WHERE cc.payment_id=p.id AND cc.status<>'cancelled') AS used
        FROM payments p WHERE p.student_id=%s AND p.status='paid'
          AND p.month < %s AND p.payment_type IN ('regular','package_8')
        ORDER BY p.month DESC,p.id DESC""",(row["student_id"],current_month)).fetchall()
    options=[]
    tariff=float(row["tariff"] or 0)
    for p in payments:
        cap=int(p["sessions_total"] or ((float(p["amount"] or 0)//tariff) if tariff>0 else 0) or 8)
        remaining=max(0,cap-int(p["used"] or 0))
        options.append({"payment":p,"cap":cap,"used":int(p["used"] or 0),"remaining":remaining})
    if request.method=="POST":
        try: pid=int(request.form.get("payment_id",""))
        except (TypeError,ValueError):
            c.close(); flash("Selecciona un pago válido."); return redirect(url_for("manual_recovery",class_id=class_id))
        chosen=c.execute("""SELECT p.*,
            (SELECT COUNT(*) FROM classes cc WHERE cc.payment_id=p.id AND cc.status<>'cancelled') AS used
            FROM payments p WHERE p.id=%s AND p.student_id=%s AND p.status='paid'
              AND p.month < %s AND p.payment_type IN ('regular','package_8')""",(pid,row["student_id"],current_month)).fetchone()
        if not chosen:
            c.close(); flash("El pago seleccionado no es válido para esta recuperación."); return redirect(url_for("manual_recovery",class_id=class_id))
        cap=int(chosen["sessions_total"] or ((float(chosen["amount"] or 0)//tariff) if tariff>0 else 0) or 8)
        used=int(chosen["used"] or 0)
        if used >= cap:
            c.close(); flash("Ese pago ya tiene todas sus clases asignadas."); return redirect(url_for("manual_recovery",class_id=class_id))
        recovery_month=chosen["month"]
        c.execute("""UPDATE classes SET payment_id=%s,payment_status='paid',is_recovery=TRUE,
            recovery_month=%s, original_date=NULL, original_time=NULL, recovery_source_class_id=NULL,
            notes=CASE WHEN COALESCE(notes,'')='' THEN %s ELSE notes || ' · ' || %s END
            WHERE id=%s AND status IN ('scheduled','rescheduled','postponed')""",
            (pid,recovery_month,f"Recuperación excepcional autorizada por el coach · período {recovery_month}",f"Recuperación excepcional autorizada por el coach · período {recovery_month}",class_id))
        c.commit(); c.close(); sync_class_to_google(class_id)
        flash(f"Clase marcada como recuperación pagada del período {recovery_month}.")
        return redirect(url_for("student_detail",sid=row["student_id"]))
    c.close()
    return render_template("manual_recovery.html",current=row,options=options)


@app.route("/entrenador/clase/<int:class_id>/eliminar", methods=["POST"])
@coach_required
def delete_class(class_id):
    c=db(); row=c.execute("SELECT * FROM classes WHERE id=%s",(class_id,)).fetchone()
    if not row: c.close(); flash("Clase no encontrada."); return redirect(url_for("dashboard"))
    c.close()
    sync_class_to_google(class_id, delete=True)
    c=db()
    c.execute("DELETE FROM classes WHERE id=%s",(class_id,)); c.commit(); c.close(); flash("Clase eliminada."); return redirect(request.referrer or url_for("dashboard"))


@app.route("/entrenador/clases/nueva", methods=["GET","POST"])
@coach_required
def new_class():
    c=db()
    if request.method=="POST":
        created_for_sync = []
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
            created_for_sync = list(created)
        except ValueError as exc:
            c.rollback(); c.close()
            msg = ("El paquete seleccionado no tiene suficientes clases disponibles." if str(exc)=="package_limit" else ("No se pudo registrar el pago. Revisa el monto." if str(exc) in ("payment_amount", "package_invalid") else ("Agrega al menos una fecha y hora válidas." if str(exc)=="invalid_class" else ("Ese horario no está disponible para reprogramar." if str(exc) in ("occupied_class", "duplicate_class") else "Agrega al menos una fecha y hora válidas."))))
            flash(msg); return redirect(url_for("new_class",student=sid))
        except Exception:
            c.rollback(); c.close(); flash("No se pudieron registrar las clases. Revisa los datos antes de volver a intentarlo."); return redirect(url_for("new_class",student=sid))
        c.close()
        sync_failures = []
        for new_class_id in created_for_sync:
            ok, error = sync_class_to_google(new_class_id)
            if not ok:
                sync_failures.append(error or "Error de sincronización")
        if sync_failures:
            flash(f"Se registraron {len(created)} clase(s). Algunas quedaron guardadas en JCFC pero no se sincronizaron con Google Calendar. Puedes reintentarlo desde Google Calendar.")
        else:
            flash(f"Se registraron {len(created)} clase(s) y se sincronizaron con Google Calendar.")
        return redirect(url_for("student_detail",sid=sid))
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
        tariff_row=c.execute("SELECT * FROM students WHERE id=%s AND status='active'",(sid,)).fetchone()
        tariff=float(tariff_row["tariff"] or 0) if tariff_row else 0
        if tariff_row:
            refresh_period_pricing(c,sid,payment_month)
        applied_tariff=tariff
        if ptype=="package_8": amount=600.0; total=8; applied_tariff=75.0
        else:
            try: applied_tariff=float(request.form.get("applied_tariff") or 0)
            except (TypeError,ValueError): applied_tariff=0
            # If the coach is registering a payment after scheduling unpaid
            # classes, reuse the tariff stored on those classes. This is what
            # allows cases such as 7 x S/60 without changing the student's
            # normal S/55 tariff.
            if applied_tariff <= 0:
                pending_rates=c.execute("""SELECT DISTINCT amount FROM classes
                    WHERE student_id=%s AND date LIKE %s
                    AND status IN ('scheduled','rescheduled','postponed')
                    AND payment_status<>'paid' ORDER BY amount""", (sid,payment_month+'%')).fetchall()
                if len(pending_rates)==1 and float(pending_rates[0]["amount"] or 0)>0:
                    applied_tariff=float(pending_rates[0]["amount"])
                else:
                    applied_tariff=tariff
            if amount<=0 or applied_tariff<=0: c.close(); flash("El monto y la tarifa aplicada deben ser válidos."); return redirect(url_for("payments"))
            total=int(amount//applied_tariff)
            if total<1: c.close(); flash("El monto no cubre al menos una clase con la tarifa aplicada."); return redirect(url_for("payments"))
        # When a regular payment is recorded, preserve the tariff actually
        # charged on the unpaid classes of that period. This makes the class
        # history, coach view and parent view agree (e.g. 7 x S/60 = S/420).
        if ptype == "regular":
            c.execute("""UPDATE classes SET amount=%s
                WHERE student_id=%s AND date LIKE %s
                  AND status<>'cancelled'
                  AND payment_status<>'paid' AND is_recovery=FALSE""",
                (applied_tariff,sid,payment_month+'%'))
        pid=c.execute("""INSERT INTO payments(student_id,month,amount,method,status,note,payment_type,sessions_total,paid_at,applied_tariff)
            VALUES(%s,%s,%s,%s,'paid',%s,%s,%s,%s,%s) RETURNING id""",(sid,payment_month,amount,request.form.get("method"),request.form.get("note",""),ptype,total,datetime.now(PERU_TZ),applied_tariff)).fetchone()["id"]
        c.commit(); sync_payment_status(c); c.commit(); flash(f"Pago registrado correctamente. Cubre {total} clase(s).")
        c.close(); return redirect(url_for("payments"))
    rows=c.execute("SELECT payments.*,students.student_name FROM payments LEFT JOIN students ON students.id=payments.student_id ORDER BY payments.month DESC,payments.id DESC").fetchall()
    students=c.execute("SELECT * FROM students WHERE status='active' ORDER BY student_name").fetchall()
    package_info=[]
    for p in rows:
        if p["payment_type"]=="package_8":
            used=c.execute("SELECT COUNT(*) AS n FROM classes WHERE payment_id=%s AND status='attended'",(p["id"],)).fetchone()["n"]
            package_info.append((p["id"],used,max(0,(p["sessions_total"] or 8)-used)))
    paid=sum(float(r["amount"] or 0) for r in rows if r["status"]=="paid")
    current_month=datetime.now(PERU_TZ).strftime("%Y-%m")
    selected_month=request.args.get("month") or current_month
    month_rows=[r for r in rows if r["status"]=="paid" and r["month"]==selected_month]
    month_total=sum(float(r["amount"] or 0) for r in month_rows)
    pending_by_student={}
    pending_tariff_by_student={}
    pending_count_by_student={}
    for srow in students:
        refresh_period_pricing(c,srow["id"],selected_month)
        pending_rows=c.execute("""SELECT amount FROM classes WHERE student_id=%s AND date LIKE %s
            AND status IN ('scheduled','rescheduled','postponed') AND payment_status<>'paid' AND is_recovery=FALSE
            ORDER BY date,time,id""",(srow["id"],selected_month+'%')).fetchall()
        pending_amount=sum(float(r["amount"] or 0) for r in pending_rows)
        rates={round(float(r["amount"] or 0),2) for r in pending_rows if float(r["amount"] or 0)>0}
        break_tariff=float(srow.get("break_tariff") or srow["tariff"] or 0)
        pending_tariff_by_student[srow["id"]]=next(iter(rates)) if len(rates)==1 else break_tariff
        if pending_amount>0:
            pending_by_student[srow["id"]]=pending_amount
            pending_count_by_student[srow["id"]]=len(pending_rows)
    pending_total=sum(pending_by_student.values())
    monthly_totals=[]
    for r in rows:
        if r["status"]!='paid':
            continue
        monthly_totals.append((r["month"], float(r["amount"] or 0)))
    by_month={}
    for month, amount in monthly_totals:
        by_month[month]=by_month.get(month,0)+amount
    months=sorted(by_month.keys(), reverse=True)
    if current_month not in months:
        months.insert(0,current_month)
    month_labels={m: datetime.strptime(m,"%Y-%m").strftime("%B de %Y").capitalize() for m in months}
    c.close()
    return render_template("payments.html",payments=rows,students=students,paid=paid,package_info=dict(package_info),
        current_month=current_month,selected_month=selected_month,month_rows=month_rows,month_total=month_total,
        monthly_totals=by_month,months=months,month_labels=month_labels,pending_total=pending_total,pending_by_student=pending_by_student,
        pending_tariff_by_student=pending_tariff_by_student,pending_count_by_student=pending_count_by_student)


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
        if action == "add_recovery":
            rdate=request.form.get("recovery_date","").strip(); rtime=request.form.get("recovery_time","").strip(); rzone=request.form.get("recovery_zone","").strip(); rplace=request.form.get("recovery_place","").strip()
            try: datetime.strptime(rdate,"%Y-%m-%d"); datetime.strptime(rtime,"%H:%M")
            except ValueError: c.close(); flash("Fecha y hora de recuperación no válidas."); return redirect(url_for("availability"))
            if not rzone or not rplace: c.close(); flash("Completa zona y lugar del espacio de recuperación."); return redirect(url_for("availability"))
            duplicate=c.execute("SELECT 1 FROM recovery_slots WHERE active=TRUE AND date=%s AND time=%s AND zone=%s",(rdate,rtime,rzone)).fetchone()
            if duplicate: c.close(); flash("Ya existe ese espacio de recuperación."); return redirect(url_for("availability"))
            c.execute("INSERT INTO recovery_slots(date,time,zone,place,active) VALUES(%s,%s,%s,%s,TRUE)",(rdate,rtime,rzone,rplace)); c.commit(); c.close(); flash("Espacio de recuperación publicado."); return redirect(url_for("availability"))
        if action == "delete_recovery":
            rid=request.form.get("recovery_id",type=int); c.execute("UPDATE recovery_slots SET active=FALSE WHERE id=%s",(rid,)); c.commit(); c.close(); flash("Espacio de recuperación eliminado."); return redirect(url_for("availability"))
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
    manual_recovery=c.execute("SELECT * FROM recovery_slots WHERE active=TRUE AND date>=%s ORDER BY date,time,id",(today.isoformat(),)).fetchall()
    c.close()
    return render_template("availability.html",availability=av,days=DAYS,turns=TURN_ORDER,agenda_status=status,current_month=current_month,recovery_slots=recovery,manual_recovery=manual_recovery)


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
        if login_is_rate_limited("parent"):
            flash("Demasiados intentos. Espera unos minutos y vuelve a intentarlo.")
            return render_template("parent_login.html")
        whatsapp=normalize_whatsapp(request.form.get("whatsapp","")); password=request.form.get("password","").strip(); c=db()
        candidates=c.execute("SELECT * FROM users WHERE role='parent'").fetchall()
        u=next((x for x in candidates if normalize_whatsapp(x["whatsapp"])==whatsapp and password_matches(x["password"], password)),None)
        if u:
            if not password_is_hashed(u["password"]):
                c.execute("UPDATE users SET password=%s WHERE id=%s", (hash_password(password), u["id"])); c.commit()
            c.close(); clear_login_failures("parent")
            session.clear(); session["role"]="parent"; session["user_id"]=u["id"]; csrf_token()
            return redirect(url_for("parent_home"))
        c.close(); record_login_failure("parent")
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
    children=c.execute("SELECT * FROM students WHERE user_id=%s AND status='active' ORDER BY student_name", (user_id,)).fetchall()
    for _child in children:
        repair_pending_recoveries(c, _child["id"])
        refresh_all_active_period_pricing(c,_child["id"])
    sync_payment_status(c); c.commit()
    if not children:
        c.close(); flash("No hay alumnos asociados a esta cuenta."); return redirect(url_for("logout"))
    child_data=[]
    for s in children:
        upcoming=c.execute("""SELECT * FROM classes WHERE student_id=%s AND date>=%s
            AND status IN ('scheduled','rescheduled','postponed') ORDER BY date,time LIMIT 60""", (s["id"],today_iso)).fetchall()
        history=c.execute("""SELECT * FROM classes WHERE student_id=%s AND date<=%s
            AND status<>'cancelled' ORDER BY date DESC,time DESC LIMIT 100""", (s["id"],today_iso)).fetchall()
        payments_rows=c.execute("SELECT * FROM payments WHERE student_id=%s ORDER BY month DESC,id DESC", (s["id"],)).fetchall()
        current_month=today.strftime("%Y-%m")
        current_payments=[p for p in payments_rows if p["month"]==current_month and p["status"]=='paid']
        current_paid=sum(float(p["amount"] or 0) for p in current_payments)
        # The payment period is separate from the physical training date.
        # Normal classes belong to their calendar month; a recovery belongs to
        # recovery_month (the original paid period), even if trained later.
        month_scheduled=c.execute("""SELECT COUNT(*) AS n FROM classes
            WHERE student_id=%s AND status<>'cancelled' AND is_recovery=FALSE AND date LIKE %s""", (s["id"],current_month+'%')).fetchone()["n"]
        month_paid_classes=c.execute("""SELECT COUNT(*) AS n FROM classes
            WHERE student_id=%s AND status<>'cancelled' AND is_recovery=FALSE AND date LIKE %s AND payment_status='paid'""", (s["id"],current_month+'%')).fetchone()["n"]
        attended_count=c.execute("""SELECT COUNT(*) AS n FROM classes
            WHERE student_id=%s AND status='attended' AND date LIKE %s""", (s["id"],current_month+'%')).fetchone()["n"]
        recovery_for_month=c.execute("""SELECT * FROM classes WHERE student_id=%s AND is_recovery=TRUE
            AND status<>'cancelled' AND COALESCE(recovery_month, original_date, date) LIKE %s
            ORDER BY date,time""", (s["id"],current_month+'%')).fetchall()
        recovery_scheduled=sum(1 for x in recovery_for_month if x["status"] in ('scheduled','rescheduled','postponed'))
        recovery_paid_scheduled=sum(1 for x in recovery_for_month if x["status"] in ('scheduled','rescheduled','postponed') and x["payment_status"]=='paid')
        recovery_pending=sum(1 for x in recovery_for_month if x["status"] in ('scheduled','rescheduled','postponed'))
        # Current-month counters exclude recoveries from the new month's package.
        # Recoveries are shown separately because their payment belongs to the
        # original month.
        scheduled_count=month_scheduled + len(recovery_for_month)
        paid_count=month_paid_classes + sum(1 for x in recovery_for_month if x["payment_status"]=='paid')
        pending_count=max(0, month_scheduled-month_paid_classes)
        pending_amount=c.execute("""SELECT COALESCE(SUM(amount),0) AS total FROM classes
            WHERE student_id=%s AND date LIKE %s AND status<>'cancelled'
              AND is_recovery=FALSE AND payment_status<>'paid'""",(s["id"],current_month+'%')).fetchone()["total"] or 0
        month_payment_status = (month_scheduled == 0 and current_paid > 0) or (month_scheduled > 0 and month_paid_classes >= month_scheduled)
        if current_paid > 0 and month_scheduled == 0:
            month_payment_status=True
        # Solo la siguiente clase que aún no ha sido reprogramada queda
        # habilitada para gestión directa del padre/madre. Una clase ya
        # reprogramada nunca vuelve a ofrecer esta opción.
        reprogrammable_id=next((x["id"] for x in upcoming if x["status"] in ("scheduled","postponed") and int(x.get("parent_reschedule_count") or 0)==0),None)
        child_data.append({
            "student":s, "upcoming":upcoming, "history":history, "payments":current_payments,
            "current_paid":current_paid, "current_month":current_month, "month_scheduled":month_scheduled,
            "month_paid_classes":month_paid_classes, "month_attended":attended_count, "month_payment_status":month_payment_status,
            "scheduled_count":scheduled_count, "paid_count":paid_count, "pending_count":pending_count, "pending_amount":pending_amount,
            "extra_count":pending_count, "recovery_pending":recovery_pending, "recovery_scheduled":recovery_scheduled,
            "recovery_paid_scheduled":recovery_paid_scheduled, "recovery_paid":sum(1 for x in recovery_for_month if x["payment_status"]=='paid'),
            "recovery_for_month":recovery_for_month, "reprogrammable_id":reprogrammable_id,
        })
    # In-app reminders: within 2 hours before the class, per child/class,
    # and persistently dismissed per parent account across devices.
    now_local = datetime.now(PERU_TZ)
    reminders = []
    for item in child_data:
        student = item["student"]
        for cl in item["upcoming"]:
            try:
                start_local = datetime.strptime(f"{cl['date']} {cl['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=PERU_TZ)
            except (ValueError, TypeError):
                continue
            delta = (start_local - now_local).total_seconds()
            if 0 < delta <= 7200:
                already_seen = c.execute("SELECT 1 FROM parent_class_reminders_seen WHERE user_id=%s AND class_id=%s", (user_id, cl["id"])).fetchone()
                if not already_seen:
                    reminders.append({"class_id": cl["id"], "student_id": student["id"], "student_name": student["student_name"], "date": cl["date"], "time": cl["time"], "place": cl.get("place") or student.get("place") or student.get("zone") or "Por confirmar", "minutes": max(1, int(delta // 60))})
    c.close(); return render_template("parent.html", children=child_data, reminders=reminders)



@app.route("/alumno/recordatorio/<int:class_id>/visto", methods=["POST"])
def parent_reminder_seen(class_id):
    if session.get("role") != "parent":
        abort(401)
    user_id = session["user_id"]
    c = db()
    owned = c.execute("""SELECT classes.id FROM classes JOIN students ON students.id=classes.student_id
        WHERE classes.id=%s AND students.user_id=%s AND classes.status IN ('scheduled','rescheduled','postponed')""", (class_id,user_id)).fetchone()
    if not owned:
        c.close(); abort(404)
    c.execute("INSERT INTO parent_class_reminders_seen(user_id,class_id) VALUES(%s,%s) ON CONFLICT(user_id,class_id) DO NOTHING", (user_id,class_id))
    c.commit(); c.close()
    return redirect(url_for("parent_home"))

@app.route("/alumno/push/clave-publica")
def parent_push_public_key():
    if session.get("role") != "parent": return {"error":"unauthorized"},401
    return {"publicKey": os.environ.get("VAPID_PUBLIC_KEY", "")}

@app.route("/alumno/push/suscribir", methods=["POST"])
def parent_push_subscribe():
    if session.get("role") != "parent": return {"error":"unauthorized"},401
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    if not endpoint or not isinstance(payload.get("keys"), dict): return {"error":"invalid subscription"},400
    import json
    c=db()
    c.execute("""INSERT INTO push_subscriptions(user_id,endpoint,subscription_json) VALUES(%s,%s,%s)
        ON CONFLICT(endpoint) DO UPDATE SET user_id=EXCLUDED.user_id,subscription_json=EXCLUDED.subscription_json""",(session["user_id"],endpoint,json.dumps(payload)))
    c.commit(); c.close()
    return {"ok":True}

@app.route("/alumno/push/desuscribir", methods=["POST"])
def parent_push_unsubscribe():
    if session.get("role") != "parent": return {"error":"unauthorized"},401
    payload=request.get_json(silent=True) or {}; endpoint=payload.get("endpoint")
    if endpoint:
        c=db(); c.execute("DELETE FROM push_subscriptions WHERE user_id=%s AND endpoint=%s",(session["user_id"],endpoint)); c.commit(); c.close()
    return {"ok":True}

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
    # A parent gets one reprogramming opportunity. Once the class has been
    # reprogrammed, it must not be offered again. The coach can still use the
    # coach-side controls for exceptional changes.
    if current["status"] not in ("scheduled","postponed") or int(current.get("parent_reschedule_count") or 0) > 0:
        c.close(); flash("Esta clase ya fue reprogramada y no puede volver a reprogramarse desde la cuenta del padre/madre."); return redirect(url_for("parent_home"))
    try: original_dt=parse_iso_datetime(current["date"],current["time"])
    except ValueError: c.close(); flash("No se pudo validar la fecha."); return redirect(url_for("parent_home"))
    now=datetime.now(PERU_TZ)
    if current["status"] != "postponed" and now >= original_dt-timedelta(hours=2):
        c.close(); return redirect(url_for("contacto_whatsapp",mensaje="Hola, Coach Juan Carlos. Tengo una emergencia y necesito coordinar una reprogramación de clase."))
    today=now.date()
    # La primera reprogramación del padre se ofrece para el siguiente mes.
    # Desde la segunda, la recuperación queda dentro del mismo mes de la clase.
    reschedule_count = int(current.get("parent_reschedule_count") or 0)
    target_month = None
    if reschedule_count == 0:
        base = date.fromisoformat(current["date"])
        year = base.year + (1 if base.month == 12 else 0)
        month = 1 if base.month == 12 else base.month + 1
        target_month = f"{year:04d}-{month:02d}"
    else:
        target_month = current["date"][:7]

    def first_month_recovery_fallback():
        # En la primera reprogramación debe existir al menos una alternativa
        # en el mes siguiente. Si la tabla de disponibilidad no devuelve
        # opciones, usamos el mismo día/hora habitual de la clase y buscamos
        # la primera fecha libre de ese patrón dentro del mes siguiente.
        if reschedule_count != 0:
            return []
        try:
            original_date = date.fromisoformat(current["date"])
            original_time = current["time"]
            weekday = original_date.weekday()
            year, month = map(int, target_month.split("-"))
            first_day = date(year, month, 1)
            d = first_day + timedelta(days=(weekday - first_day.weekday()) % 7)
            while d.month == month:
                occupied = c.execute(
                    "SELECT 1 FROM classes WHERE date=%s AND time=%s AND status IN ('scheduled','rescheduled') AND id<>%s LIMIT 1",
                    (d.isoformat(), original_time, class_id)
                ).fetchone()
                if not occupied:
                    return [{"date":d.isoformat(),"day":DAYS[d.weekday()],"time":original_time,
                             "zone":(current["student_zone"] or "").strip(),"turn":turn_for_time(original_time),"kind":"fallback"}]
                d += timedelta(days=7)
        except Exception:
            return []
        return []

    if request.method=="POST":
        new_date=request.form.get("date","").strip(); new_time=request.form.get("time","").strip(); zone=(current["student_zone"] or "").strip()
        try: selected=date.fromisoformat(new_date); selected_day=DAYS[selected.weekday()]
        except (ValueError,IndexError): c.close(); flash("La fecha seleccionada no es válida."); return redirect(url_for("reschedule",class_id=class_id))
        if selected<today: c.close(); flash("La nueva fecha no puede ser anterior a hoy."); return redirect(url_for("reschedule",class_id=class_id))
        available_slots=[x for x in reschedule_slots(c,current,today) if x["date"][:7] == target_month]
        if not available_slots:
            available_slots=first_month_recovery_fallback()
        allowed=any(x["date"]==new_date and x["time"]==new_time for x in available_slots)
        occupied=c.execute("SELECT 1 FROM classes WHERE date=%s AND time=%s AND status IN ('scheduled','rescheduled') AND id<>%s LIMIT 1",(new_date,new_time,class_id)).fetchone()
        if not allowed or occupied: c.close(); flash("Ese horario ya no está disponible para reprogramar."); return redirect(url_for("reschedule",class_id=class_id))
        old_date=current["date"]; old_time=current["time"]
        selected_recovery_slot=next((x for x in available_slots if x["date"]==new_date and x["time"]==new_time and x.get("kind")=="manual_recovery"),None)
        c.execute("UPDATE classes SET date=%s,time=%s,status='rescheduled',parent_reschedule_count=COALESCE(parent_reschedule_count,0)+1,original_date=COALESCE(original_date,%s),original_time=COALESCE(original_time,%s),recovery_slot_id=%s WHERE id=%s",(new_date,new_time,old_date,old_time,selected_recovery_slot.get("recovery_slot_id") if selected_recovery_slot else None,class_id))
        if selected_recovery_slot: c.execute("UPDATE recovery_slots SET active=FALSE WHERE id=%s",(selected_recovery_slot["recovery_slot_id"],))
        c.execute("""INSERT INTO coach_notifications(kind,class_id,student_id,title,message)
            VALUES(%s,%s,%s,%s,%s)""", (
                "reschedule", class_id, current["student_id"], "Nueva reprogramación",
                f"{current['student_name']} cambió su clase de {display_date(old_date)} · {display_time(old_time)} a {display_date(new_date)} · {display_time(new_time)} · {zone or 'Zona por indicar'}."
            ))
        c.commit(); c.close()
        sync_class_to_google(class_id)
        message=(f"⚽ Hola, Coach Juan Carlos.\n\nSe reprogramó una clase desde la app.\n\nAlumno: {current['student_name']}\nZona: {zone}\nClase anterior: {display_date(old_date)} · {display_time(old_time)}.\nNueva fecha: {selected_day.lower()} {selected.day} de {selected.strftime('%B').lower()} de {selected.year}.\nNueva hora: {display_time(new_time)}.\n\nLa solicitud fue realizada desde la aplicación de JC Fútbol Coach.")
        return render_template("rescheduled.html",current=current,old_date=old_date,new_date=new_date,new_time=new_time,zone=zone,whatsapp_url=f"https://wa.me/{COACH_WHATSAPP}?text={quote(message)}")
    slots=[x for x in reschedule_slots(c,current,today) if x["date"][:7] == target_month]
    if not slots:
        slots=first_month_recovery_fallback()
    c.close(); return render_template("reschedule.html",current=current,slots=slots,reschedule_count=reschedule_count,target_month=target_month)



@app.route("/entrenador/clase/<int:class_id>/reprogramar", methods=["GET","POST"])
@coach_required
def coach_reschedule(class_id):
    c=db()
    current=c.execute("""SELECT classes.*,students.student_name,students.zone AS student_zone,students.parent_name
        FROM classes JOIN students ON students.id=classes.student_id WHERE classes.id=%s""",(class_id,)).fetchone()
    if not current: c.close(); flash("Clase no encontrada."); return redirect(url_for("dashboard"))
    if request.method=="POST":
        new_date=request.form.get("date","").strip(); new_time=request.form.get("time","").strip()
        try:
            selected=date.fromisoformat(new_date)
            datetime.strptime(new_time, "%H:%M")
        except (ValueError,TypeError):
            c.close(); flash("La fecha u hora no son válidas."); return redirect(url_for("coach_reschedule",class_id=class_id))
        if selected < datetime.now(PERU_TZ).date():
            c.close(); flash("No puedes reprogramar una clase a una fecha anterior a hoy."); return redirect(url_for("coach_reschedule",class_id=class_id))
        if selected.weekday() > 5:
            c.close(); flash("No se pueden agendar clases los domingos."); return redirect(url_for("coach_reschedule",class_id=class_id))
        # Coach has the same free date/time picker as Edit Class; only real conflicts block the save.
        conflict=c.execute("""SELECT id,student_id,session_group_id FROM classes
            WHERE date=%s AND time=%s AND id<>%s
            AND status IN ('scheduled','rescheduled','postponed') LIMIT 1""",
            (new_date,new_time,class_id)).fetchone()
        if conflict:
            same_group=bool(current.get("session_group_id") and conflict.get("session_group_id")==current.get("session_group_id"))
            same_student=conflict["student_id"]==current["student_id"]
            # A coach may intentionally place an individual class into an
            # existing class at the same time. That turns the destination into
            # a Clase compartida instead of treating it as a scheduling error.
            if not (same_group or same_student):
                target_group=conflict.get("session_group_id") or str(uuid.uuid4())
                c.execute("UPDATE classes SET session_group_id=%s WHERE date=%s AND time=%s AND status IN ('scheduled','rescheduled','postponed')",(target_group,new_date,new_time))
                current_group_to_merge=target_group
            else:
                current_group_to_merge=conflict.get("session_group_id") or current.get("session_group_id")
        else:
            current_group_to_merge=None
        old_date,old_time=current["date"],current["time"]
        move_group=request.form.get("move_group") == "yes"
        if conflict and not (current.get("session_group_id") and conflict.get("session_group_id")==current.get("session_group_id")) and not move_group:
            c.execute("UPDATE classes SET date=%s,time=%s,status='rescheduled',is_recovery=TRUE,original_date=COALESCE(original_date,%s),original_time=COALESCE(original_time,%s),session_group_id=%s WHERE id=%s",(new_date,new_time,old_date,old_time,current_group_to_merge,class_id))
            attach_recovery_payment(c,class_id)
            sync_ids=[class_id]
        elif move_group and current.get("session_group_id"):
            rows=c.execute("SELECT id FROM classes WHERE session_group_id=%s AND status IN ('scheduled','rescheduled','postponed')",(current["session_group_id"],)).fetchall()
            c.execute("UPDATE classes SET date=%s,time=%s,status='rescheduled',is_recovery=TRUE,original_date=COALESCE(original_date,%s),original_time=COALESCE(original_time,%s) WHERE session_group_id=%s AND status IN ('scheduled','rescheduled','postponed')",(new_date,new_time,old_date,old_time,current["session_group_id"]))
            sync_ids=[r["id"] for r in rows]
        else:
            c.execute("UPDATE classes SET date=%s,time=%s,status='rescheduled',is_recovery=TRUE,original_date=COALESCE(original_date,%s),original_time=COALESCE(original_time,%s) WHERE id=%s",(new_date,new_time,old_date,old_time,class_id))
            attach_recovery_payment(c,class_id)
            sync_ids=[class_id]
        manual_slot=c.execute("SELECT id FROM recovery_slots WHERE active=TRUE AND date=%s AND time=%s LIMIT 1",(new_date,new_time)).fetchone()
        if manual_slot:
            c.execute("UPDATE recovery_slots SET active=FALSE WHERE id=%s",(manual_slot["id"],))
            c.execute("UPDATE classes SET recovery_slot_id=%s WHERE id=ANY(%s)",(manual_slot["id"],sync_ids))
        c.commit(); c.close()
        for sync_id in sync_ids: sync_class_to_google(sync_id)
        flash(f"{'Clase compartida' if move_group and current.get('session_group_id') else 'Clase'} reprogramada para {display_date(new_date)} · {display_time(new_time)}.")
        return redirect(url_for("student_detail",sid=current["student_id"]))
    shared_count=0 if not current.get("session_group_id") else c.execute("SELECT COUNT(*) AS n FROM classes WHERE session_group_id=%s AND status<>'cancelled'",(current["session_group_id"],)).fetchone()["n"]
    c.close()
    return render_template("reschedule.html",current=current,slots=[],coach_mode=True,now_date=datetime.now(PERU_TZ).date().isoformat(),shared_count=shared_count)


@app.route("/entrenador/google-calendar/sincronizar", methods=["POST"])
@coach_required
def google_calendar_sync_all():
    c=db()
    ids=c.execute("SELECT id FROM classes WHERE status IN ('scheduled','rescheduled','postponed') ORDER BY date,time,id").fetchall()
    c.close()
    ok=0; failed=0
    for item in ids:
        success, _ = sync_class_to_google(item["id"])
        if success: ok += 1
        else: failed += 1
    if failed:
        flash(f"Sincronización terminada: {ok} clase(s) sincronizada(s) y {failed} con error. Revisa la conexión de Google Calendar.")
    else:
        flash(f"Sincronización terminada: {ok} clase(s) sincronizada(s) con Google Calendar.")
    return redirect(url_for("google_calendar_settings"))

@app.route("/entrenador/google-calendar")
@coach_required
def google_calendar_settings():
    c=db()
    setting=_google_setting(c,session["user_id"])
    connected=bool(setting and setting.get("refresh_token"))
    c.close()
    return render_template("google_calendar.html",calendar_name=GOOGLE_CALENDAR_NAME,
        connected=connected,configured=google_oauth_configured())

@app.route("/entrenador/google-calendar/conectar")
@coach_required
def google_calendar_connect():
    if not google_oauth_configured():
        flash("Falta configurar GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET y GOOGLE_REDIRECT_URI en el entorno.")
        return redirect(url_for("google_calendar_settings"))
    state=token_hex(24)
    session["google_oauth_state"]=state
    from urllib.parse import urlencode
    params={"client_id":os.environ["GOOGLE_CLIENT_ID"],"redirect_uri":os.environ["GOOGLE_REDIRECT_URI"],
        "response_type":"code","scope":GOOGLE_SCOPES,"access_type":"offline","prompt":"consent",
        "include_granted_scopes":"true","state":state}
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?"+urlencode(params))

@app.route("/oauth/google/callback")
@coach_required
def google_calendar_callback():
    if request.args.get("error"):
        flash("No se autorizó Google Calendar.")
        return redirect(url_for("google_calendar_settings"))
    state=request.args.get("state","")
    if not state or state != session.pop("google_oauth_state",None):
        abort(400,"Estado OAuth inválido.")
    code=request.args.get("code")
    if not code or not google_oauth_configured():
        flash("No se pudo completar la autorización de Google.")
        return redirect(url_for("google_calendar_settings"))
    try:
        token_response=requests.post("https://oauth2.googleapis.com/token",data={
            "code":code,"client_id":os.environ["GOOGLE_CLIENT_ID"],
            "client_secret":os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri":os.environ["GOOGLE_REDIRECT_URI"],"grant_type":"authorization_code"
        },timeout=25)
        token_response.raise_for_status()
        tokens=token_response.json()
        c=db()
        existing=_google_setting(c,session["user_id"])
        refresh=tokens.get("refresh_token") or (_token_decrypt(existing.get("refresh_token")) if existing else None)
        if not refresh:
            c.close()
            flash("Google no devolvió permiso de actualización. Desconecta el acceso de la app en tu cuenta Google y vuelve a conectar.")
            return redirect(url_for("google_calendar_settings"))
        expiry=datetime.now(PERU_TZ).replace(tzinfo=None)+timedelta(seconds=int(tokens.get("expires_in",3600)))
        c.execute("""INSERT INTO google_calendar_settings(coach_user_id,access_token,refresh_token,token_expiry)
            VALUES(%s,%s,%s,%s) ON CONFLICT(coach_user_id) DO UPDATE SET
            access_token=EXCLUDED.access_token,refresh_token=EXCLUDED.refresh_token,token_expiry=EXCLUDED.token_expiry,updated_at=CURRENT_TIMESTAMP""",
            (session["user_id"],_token_encrypt(tokens.get("access_token")),_token_encrypt(refresh),expiry))
        c.commit()
        setting=_google_setting(c,session["user_id"])
        _ensure_google_calendar(c,setting)
        c.commit(); c.close()
        # Backfill all active/future classes. This also initializes events for existing schedule.
        c=db()
        ids=c.execute("SELECT id FROM classes WHERE status IN ('scheduled','rescheduled','postponed') ORDER BY date,time,id").fetchall()
        c.close()
        ok=0; failed=0
        for item in ids:
            success, _ = sync_class_to_google(item["id"])
            ok += 1 if success else 0
            failed += 0 if success else 1
        if failed:
            flash(f"Google Calendar conectado. {ok} clase(s) sincronizada(s); {failed} quedaron pendientes de sincronización.")
        else:
            flash(f"Google Calendar conectado. {ok} clase(s) sincronizada(s) correctamente.")
    except Exception as exc:
        app.logger.exception("Google OAuth callback failed: %s",exc)
        flash("No se pudo conectar Google Calendar. Revisa las credenciales OAuth configuradas en el entorno.")
    return redirect(url_for("google_calendar_settings"))

@app.route("/entrenador/google-calendar/desconectar",methods=["POST"])
@coach_required
def google_calendar_disconnect():
    c=db()
    setting=_google_setting(c,session["user_id"])
    if setting:
        refresh=_token_decrypt(setting.get("refresh_token"))
        if refresh:
            try: requests.post("https://oauth2.googleapis.com/revoke",data={"token":refresh},timeout=10)
            except Exception: pass
        c.execute("DELETE FROM google_calendar_settings WHERE coach_user_id=%s",(session["user_id"],))
        c.commit()
    c.close()
    flash("Google Calendar desconectado. Los eventos ya creados en Google no se eliminaron.")
    return redirect(url_for("google_calendar_settings"))


@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("home"))


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=getattr(error, "description", "Solicitud no válida."), back_url=url_for("home")), 400

@app.errorhandler(404)
def page_not_found(error):
    return render_template("error.html", code=404, message="No encontramos esa página.", back_url=url_for("home")), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template("error.html", code=500, message="Ocurrió un problema al abrir esta pantalla. Si acabas de guardar información, revisa primero la ficha antes de repetir el registro.", back_url=url_for("home")), 500


init_db()

if __name__ == "__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
