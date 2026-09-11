from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
import re
from urllib.parse import quote
from functools import wraps
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-this")

DATABASE_URL = os.environ.get("DATABASE_URL")

# WhatsApp de JC Fútbol Coach para solicitudes y contacto.
COACH_WHATSAPP = "51993757225"

# Tarifas públicas para alumnos nuevos.
# La tarifa real de cada alumno se asigna y modifica manualmente desde el panel.
PRICING = {
    "new_students": [
        {"age": "3 años", "duration": "30 min", "price": "S/ 50"},
        {"age": "4–5 años", "duration": "45 min", "price": "S/ 60"},
        {"age": "Desde 6 años hasta adultos", "duration": "1 hora", "price": "S/ 70"},
    ],
    "plans": [
        {"sessions": "4 sesiones", "price": "S/ 280"},
        {"sessions": "8 sesiones", "price": "S/ 520"},
    ],
}

DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
PUBLIC_ZONES = ["San Borja", "Surco / Chacarilla", "Barranco"]


def db():
    if not DATABASE_URL:
        raise RuntimeError("Falta la variable de entorno DATABASE_URL.")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    c = db()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      role TEXT NOT NULL,
      name TEXT NOT NULL,
      whatsapp TEXT,
      password TEXT,
      dni TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS students(
      id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      user_id INTEGER,
      parent_name TEXT NOT NULL,
      parent_whatsapp TEXT NOT NULL,
      student_name TEXT NOT NULL,
      age INTEGER NOT NULL,
      zone TEXT,
      mode TEXT,
      place TEXT,
      tariff DOUBLE PRECISION NOT NULL,
      status TEXT NOT NULL DEFAULT 'active',
      photo_consent TEXT NOT NULL DEFAULT 'No',
      dni TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS classes(
      id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      student_id INTEGER,
      date TEXT NOT NULL,
      time TEXT NOT NULL,
      mode TEXT,
      place TEXT,
      amount DOUBLE PRECISION NOT NULL,
      status TEXT NOT NULL DEFAULT 'scheduled',
      notes TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS payments(
      id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      student_id INTEGER,
      month TEXT NOT NULL,
      amount DOUBLE PRECISION NOT NULL,
      method TEXT,
      status TEXT NOT NULL DEFAULT 'paid',
      note TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS waitlist(
      id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      parent_name TEXT NOT NULL,
      whatsapp TEXT NOT NULL,
      student_name TEXT NOT NULL,
      age INTEGER NOT NULL,
      zone TEXT,
      preferences TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      status TEXT DEFAULT 'waiting'
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS teams(
      id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      name TEXT NOT NULL,
      contact TEXT NOT NULL,
      whatsapp TEXT NOT NULL,
      players INTEGER NOT NULL,
      service TEXT NOT NULL,
      place TEXT NOT NULL,
      date TEXT NOT NULL,
      time TEXT NOT NULL,
      amount DOUBLE PRECISION NOT NULL,
      payment_status TEXT DEFAULT 'pending',
      notes TEXT
    )
    """)
    # Disponibilidad permanente del negocio. Ya no se guarda en session.
    c.execute("""
    CREATE TABLE IF NOT EXISTS availability(
      id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      zone TEXT NOT NULL,
      day TEXT NOT NULL,
      time TEXT NOT NULL,
      active BOOLEAN NOT NULL DEFAULT TRUE
    )
    """)
    c.commit()

    # Cuenta del entrenador administrada por variables de entorno de Render.
    coach_user = os.environ.get("COACH_USER", "").strip()
    coach_password = os.environ.get("COACH_PASSWORD", "").strip()
    if coach_user and coach_password:
        existing = c.execute(
            "SELECT id FROM users WHERE role='coach' AND name=%s", (coach_user,)
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE users SET password=%s WHERE id=%s",
                (coach_password, existing["id"]),
            )
        else:
            c.execute(
                "INSERT INTO users(role,name,whatsapp,password,dni) VALUES(%s,%s,%s,%s,%s)",
                ("coach", coach_user, "", coach_password, ""),
            )
        c.commit()
    c.close()


def coach_required(f):
    @wraps(f)
    def w(*a, **kw):
        if session.get("role") != "coach":
            return redirect(url_for("coach_login"))
        return f(*a, **kw)
    return w


def normalize_whatsapp(value):
    return re.sub(r"\D", "", value or "")


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

        if not parent_name or not whatsapp or not student_name or not age or not place:
            c.close()
            flash("Completa los datos obligatorios.")
            return redirect(url_for("nuevo"))

        # La solicitud se coordina por WhatsApp. El registro completo del alumno
        # lo hace el entrenador manualmente después de conversar con el padre/madre.
        message = (
            "Hola JC, quiero solicitar un entrenamiento de fútbol.\n\n"
            f"Padre/madre: {parent_name}\n"
            f"WhatsApp: {whatsapp}\n"
            f"Alumno: {student_name}\n"
            f"Edad: {age}\n"
            f"Zona: {zone}\n"
            f"Modalidad: {mode}\n"
            f"Parque o dirección: {place}\n"
            f"Horario de interés: {schedule or 'Por coordinar'}\n"
            f"Fotos/videos: {photo_consent or 'Por coordinar'}"
        )
        c.close()
        return redirect(f"https://wa.me/{COACH_WHATSAPP}?text={quote(message)}")

    availability_rows = c.execute(
        """SELECT * FROM availability WHERE active=TRUE
           ORDER BY CASE day
             WHEN 'Lunes' THEN 1 WHEN 'Martes' THEN 2 WHEN 'Miércoles' THEN 3
             WHEN 'Jueves' THEN 4 WHEN 'Viernes' THEN 5 WHEN 'Sábado' THEN 6
             WHEN 'Domingo' THEN 7 ELSE 8 END, time, zone"""
    ).fetchall()
    c.close()
    return render_template(
        "new.html",
        availability=availability_rows,
        pricing=PRICING,
        coach_whatsapp=COACH_WHATSAPP,
    )


@app.route("/espera", methods=["GET", "POST"])
def espera():
    if request.method == "POST":
        c = db()
        c.execute(
            """INSERT INTO waitlist(parent_name,whatsapp,student_name,age,zone,preferences)
               VALUES(%s,%s,%s,%s,%s,%s)""",
            (
                request.form["parent_name"], request.form["whatsapp"],
                request.form["student_name"], request.form["age"],
                request.form["zone"], request.form["preferences"],
            ),
        )
        c.commit()
        c.close()
        return redirect(url_for("solicitud_recibida"))
    return render_template("waitlist.html")


@app.route("/equipo", methods=["GET", "POST"])
def equipo():
    if request.method == "POST":
        c = db()
        c.execute(
            """INSERT INTO teams(name,contact,whatsapp,players,service,place,date,time,amount,notes)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                request.form["name"], request.form["contact"], request.form["whatsapp"],
                request.form["players"], request.form["service"], request.form["place"],
                request.form["date"], request.form["time"], request.form["amount"] or 0,
                request.form["notes"],
            ),
        )
        c.commit()
        c.close()
        return redirect(url_for("solicitud_recibida"))
    return render_template("team.html")


@app.route("/recibida")
def solicitud_recibida():
    return render_template("received.html")


@app.route("/contacto-whatsapp")
def contacto_whatsapp():
    message = request.args.get(
        "mensaje", "Hola JC, quisiera consultar por las clases de fútbol personalizadas."
    )
    return redirect(f"https://wa.me/{COACH_WHATSAPP}?text={quote(message)}")


@app.route("/entrenador/login", methods=["GET", "POST"])
def coach_login():
    if request.method == "POST":
        c = db()
        u = c.execute(
            "SELECT * FROM users WHERE role='coach' AND name=%s AND password=%s",
            (request.form["user"], request.form["password"]),
        ).fetchone()
        c.close()
        if u:
            session["role"] = "coach"
            session["user_id"] = u["id"]
            return redirect(url_for("dashboard"))
        flash("Usuario o contraseña incorrectos.")
    return render_template("coach_login.html")


@app.route("/entrenador")
@coach_required
def dashboard():
    c = db()
    students = c.execute("SELECT * FROM students WHERE status='active' ORDER BY student_name").fetchall()
    classes = c.execute(
        """SELECT classes.*,students.student_name FROM classes
           LEFT JOIN students ON students.id=classes.student_id
           ORDER BY date,time LIMIT 20"""
    ).fetchall()
    wait = c.execute("SELECT * FROM waitlist WHERE status='waiting' ORDER BY id DESC").fetchall()
    teams = c.execute("SELECT * FROM teams ORDER BY date,time LIMIT 10").fetchall()
    c.close()
    return render_template("dashboard.html", students=students, classes=classes, wait=wait, teams=teams)


@app.route("/entrenador/alumnos")
@coach_required
def students():
    c = db()
    rows = c.execute("SELECT * FROM students ORDER BY status DESC, student_name").fetchall()
    c.close()
    return render_template("students.html", students=rows)


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
        u = c.execute(
            "SELECT * FROM users WHERE role='parent' AND whatsapp=%s", (parent_whatsapp,)
        ).fetchone()
        if u:
            user_id = u["id"]
            c.execute(
                "UPDATE users SET name=%s, password=%s, dni=%s WHERE id=%s",
                (parent_name, dni, dni, user_id),
            )
        else:
            user_id = c.execute(
                """INSERT INTO users(role,name,whatsapp,password,dni)
                   VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                ("parent", parent_name, parent_whatsapp, dni, dni),
            ).fetchone()["id"]
        c.execute(
            """INSERT INTO students(user_id,parent_name,parent_whatsapp,student_name,dni,age,zone,mode,place,tariff,photo_consent)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                user_id, parent_name, parent_whatsapp, student_name, dni,
                int(request.form["age"]), request.form["zone"], request.form["mode"],
                request.form["place"], float(request.form["tariff"]), request.form["photo_consent"],
            ),
        )
        c.commit()
        c.close()
        flash("Alumno creado. Puede ingresar con su WhatsApp y DNI.")
        return redirect(url_for("students"))
    return render_template("new_student.html")


@app.route("/entrenador/alumno/<int:sid>/editar", methods=["GET", "POST"])
@coach_required
def edit_student(sid):
    c = db()
    s = c.execute("SELECT * FROM students WHERE id=%s", (sid,)).fetchone()
    if not s:
        c.close()
        flash("Alumno no encontrado.")
        return redirect(url_for("students"))
    if request.method == "POST":
        parent_name = request.form["parent_name"].strip()
        parent_whatsapp = request.form["parent_whatsapp"].strip()
        student_name = request.form["student_name"].strip()
        dni = request.form["dni"].strip()
        if not parent_name or not parent_whatsapp or not student_name or not dni:
            c.close()
            flash("Completa nombre, WhatsApp, alumno y DNI.")
            return redirect(url_for("edit_student", sid=sid))
        u = c.execute(
            "SELECT * FROM users WHERE role='parent' AND whatsapp=%s", (parent_whatsapp,)
        ).fetchone()
        if u:
            user_id = u["id"]
            c.execute(
                "UPDATE users SET name=%s, password=%s, dni=%s WHERE id=%s",
                (parent_name, dni, dni, user_id),
            )
        else:
            user_id = c.execute(
                """INSERT INTO users(role,name,whatsapp,password,dni)
                   VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                ("parent", parent_name, parent_whatsapp, dni, dni),
            ).fetchone()["id"]
        c.execute(
            """UPDATE students SET user_id=%s, parent_name=%s, parent_whatsapp=%s,
               student_name=%s, dni=%s, age=%s, zone=%s, mode=%s, place=%s,
               tariff=%s, photo_consent=%s WHERE id=%s""",
            (
                user_id, parent_name, parent_whatsapp, student_name, dni,
                int(request.form["age"]), request.form["zone"], request.form["mode"],
                request.form["place"], float(request.form["tariff"]), request.form["photo_consent"], sid,
            ),
        )
        c.commit()
        c.close()
        flash("Alumno actualizado. Puede ingresar con su WhatsApp y DNI.")
        return redirect(url_for("student_detail", sid=sid))
    c.close()
    return render_template("edit_student.html", s=s)


@app.route("/entrenador/alumno/<int:sid>")
@coach_required
def student_detail(sid):
    c = db()
    s = c.execute("SELECT * FROM students WHERE id=%s", (sid,)).fetchone()
    classes = c.execute(
        "SELECT * FROM classes WHERE student_id=%s ORDER BY date DESC,time DESC", (sid,)
    ).fetchall()
    payments = c.execute(
        "SELECT * FROM payments WHERE student_id=%s ORDER BY month DESC", (sid,)
    ).fetchall()
    c.close()
    return render_template("student_detail.html", s=s, classes=classes, payments=payments)


@app.route("/entrenador/clases/nueva", methods=["GET", "POST"])
@coach_required
def new_class():
    c = db()
    if request.method == "POST":
        sid = int(request.form["student_id"])
        s = c.execute("SELECT * FROM students WHERE id=%s", (sid,)).fetchone()
        c.execute(
            """INSERT INTO classes(student_id,date,time,mode,place,amount,status,notes)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                sid, request.form["date"], request.form["time"], request.form["mode"],
                request.form["place"], s["tariff"], "scheduled", request.form["notes"],
            ),
        )
        c.commit()
        c.close()
        return redirect(url_for("dashboard"))
    students = c.execute(
        "SELECT * FROM students WHERE status='active' ORDER BY student_name"
    ).fetchall()
    c.close()
    return render_template("new_class.html", students=students)


@app.route("/entrenador/pagos", methods=["GET", "POST"])
@coach_required
def payments():
    c = db()
    if request.method == "POST":
        c.execute(
            """INSERT INTO payments(student_id,month,amount,method,status,note)
               VALUES(%s,%s,%s,%s,%s,%s)""",
            (
                request.form["student_id"], request.form["month"], request.form["amount"],
                request.form["method"], "paid", request.form["note"],
            ),
        )
        c.commit()
    rows = c.execute(
        """SELECT payments.*,students.student_name FROM payments
           LEFT JOIN students ON students.id=payments.student_id
           ORDER BY payments.month DESC,payments.id DESC"""
    ).fetchall()
    students = c.execute(
        "SELECT * FROM students WHERE status='active' ORDER BY student_name"
    ).fetchall()
    c.close()
    paid = sum(r["amount"] for r in rows if r["status"] == "paid")
    return render_template("payments.html", payments=rows, students=students, paid=paid)


@app.route("/entrenador/disponibilidad", methods=["GET", "POST"])
@coach_required
def availability():
    c = db()
    if request.method == "POST":
        zone = request.form.get("zone", "").strip()
        day = request.form.get("day", "").strip()
        time = request.form.get("time", "").strip()
        if not zone or not day or not time:
            c.close()
            flash("Completa zona, día y hora.")
            return redirect(url_for("availability"))
        c.execute(
            "INSERT INTO availability(zone,day,time,active) VALUES(%s,%s,%s,TRUE)",
            (zone, day, time),
        )
        c.commit()
        c.close()
        flash("Disponibilidad agregada.")
        return redirect(url_for("availability"))

    av = c.execute(
        """SELECT * FROM availability WHERE active=TRUE
           ORDER BY CASE day
             WHEN 'Lunes' THEN 1 WHEN 'Martes' THEN 2 WHEN 'Miércoles' THEN 3
             WHEN 'Jueves' THEN 4 WHEN 'Viernes' THEN 5 WHEN 'Sábado' THEN 6
             WHEN 'Domingo' THEN 7 ELSE 8 END, time, zone"""
    ).fetchall()
    c.close()
    return render_template("availability.html", availability=av, zones=PUBLIC_ZONES, days=DAYS)


@app.route("/entrenador/disponibilidad/eliminar/<int:availability_id>", methods=["POST"])
@coach_required
def delete_availability(availability_id):
    c = db()
    c.execute("DELETE FROM availability WHERE id=%s", (availability_id,))
    c.commit()
    c.close()
    flash("Disponibilidad eliminada.")
    return redirect(url_for("availability"))


@app.route("/entrenador/espera")
@coach_required
def wait_admin():
    c = db()
    rows = c.execute(
        "SELECT * FROM waitlist WHERE status='waiting' ORDER BY id DESC"
    ).fetchall()
    c.close()
    return render_template("wait_admin.html", wait=rows)


@app.route("/entrenador/equipos", methods=["GET", "POST"])
@coach_required
def teams():
    c = db()
    if request.method == "POST":
        c.execute(
            """INSERT INTO teams(name,contact,whatsapp,players,service,place,date,time,amount,payment_status,notes)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                request.form["name"], request.form["contact"], request.form["whatsapp"],
                request.form["players"], request.form["service"], request.form["place"],
                request.form["date"], request.form["time"], request.form["amount"] or 0,
                request.form["payment_status"], request.form["notes"],
            ),
        )
        c.commit()
    rows = c.execute("SELECT * FROM teams ORDER BY date,time").fetchall()
    c.close()
    return render_template("teams.html", teams=rows)


@app.route("/alumno/login", methods=["GET", "POST"])
def parent_login():
    if request.method == "POST":
        whatsapp = normalize_whatsapp(request.form.get("whatsapp", ""))
        password = request.form.get("password", "").strip()
        c = db()
        candidates = c.execute("SELECT * FROM users WHERE role='parent'").fetchall()
        c.close()
        u = next(
            (x for x in candidates if normalize_whatsapp(x["whatsapp"]) == whatsapp and x["password"] == password),
            None,
        )
        if u:
            session["role"] = "parent"
            session["user_id"] = u["id"]
            return redirect(url_for("parent_home"))
        flash("Datos incorrectos. Usa el WhatsApp registrado y tu contraseña.")
    return render_template("parent_login.html")


@app.route("/alumno/cambiar-contrasena", methods=["GET", "POST"])
def change_password():
    if session.get("role") != "parent":
        return redirect(url_for("parent_login"))
    if request.method == "POST":
        current = request.form.get("current_password", "").strip()
        new_password = request.form.get("new_password", "").strip()
        confirm = request.form.get("confirm_password", "").strip()
        if not current or not new_password or not confirm:
            flash("Completa todos los campos.")
            return redirect(url_for("change_password"))
        if new_password != confirm:
            flash("Las nuevas contraseñas no coinciden.")
            return redirect(url_for("change_password"))
        if len(new_password) < 6:
            flash("La nueva contraseña debe tener al menos 6 caracteres.")
            return redirect(url_for("change_password"))
        c = db()
        u = c.execute(
            "SELECT * FROM users WHERE id=%s AND role='parent'", (session["user_id"],)
        ).fetchone()
        if not u or u["password"] != current:
            c.close()
            flash("La contraseña actual es incorrecta.")
            return redirect(url_for("change_password"))
        c.execute("UPDATE users SET password=%s WHERE id=%s", (new_password, u["id"]))
        c.commit()
        c.close()
        flash("Contraseña actualizada correctamente.")
        return redirect(url_for("parent_home"))
    return render_template("change_password.html")


@app.route("/alumno")
def parent_home():
    if session.get("role") != "parent":
        return redirect(url_for("parent_login"))
    c = db()
    s = c.execute(
        "SELECT * FROM students WHERE user_id=%s LIMIT 1", (session["user_id"],)
    ).fetchone()
    if not s:
        c.close()
        flash("No hay un alumno asociado a esta cuenta.")
        return redirect(url_for("logout"))
    classes = c.execute(
        "SELECT * FROM classes WHERE student_id=%s ORDER BY date,time", (s["id"],)
    ).fetchall()
    payments = c.execute(
        "SELECT * FROM payments WHERE student_id=%s ORDER BY month DESC", (s["id"],)
    ).fetchall()
    c.close()
    return render_template("parent.html", s=s, classes=classes, payments=payments)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.context_processor
def globals():
    return {
        "app_name": "JC Fútbol Coach",
        "coach_whatsapp": COACH_WHATSAPP,
        "pricing": PRICING,
    }


# Render/Gunicorn imports this module; initialize the database when the app starts.
init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
