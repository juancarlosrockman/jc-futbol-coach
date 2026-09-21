# JC Fútbol Coach — versión 12.2

Base real Flask + PostgreSQL/Supabase para desplegar en GitHub/Render.

## Estructura
- `app.py` — aplicación y rutas.
- `templates/` — interfaz pública, alumno y Coach.
- `static/logo.jpg` — logo.
- `static/jc_coach_photo.png` — foto del Coach usada en el panel.
- `requirements.txt` — dependencias.
- `gunicorn.conf.py` — configuración de Gunicorn.

## Render
**Build Command**
```
pip install -r requirements.txt
```

**Start Command**
```
gunicorn app:app
```

## Variables de entorno
- `DATABASE_URL` — cadena de conexión PostgreSQL de Supabase.
- `SECRET_KEY` — clave secreta larga y aleatoria.
- `COACH_USER` — usuario privado del Coach.
- `COACH_PASSWORD` — contraseña privada del Coach.

No colocar estas credenciales dentro del código ni subir un archivo `.env` a GitHub.

## Flujo de trabajo
1. Descargar el ZIP.
2. Descomprimirlo.
3. Subir/reemplazar los archivos del repositorio de GitHub manteniendo `app.py`, `templates/`, `static/`, `requirements.txt` y `gunicorn.conf.py`.
4. Render detectará el cambio y hará un nuevo deploy.
5. Verificar primero el login del Coach y luego el login de un alumno antes de registrar datos reales.

## Cambios de esta versión
- Corregida la lectura del estado `is_full` de la agenda.
- Corregido el monto visual del paquete de 8 clases a S/600.
- El alumno actual ya no entra en la lista de espera destinada a nuevos alumnos; cuando no encuentra horario, se le deriva a coordinación por WhatsApp.
- El acceso del Coach está disponible desde el menú público; la ruta privada se conserva.
- Corregida la edición de clases para evitar mover una clase sobre otra actividad ocupada, salvo clases que pertenezcan al mismo grupo.
- Se incorpora la foto aprobada del Coach (polo blanco, estadio de fondo) en la portada y el panel.
- Se conserva la lógica de Supabase/PostgreSQL, cuentas de padres, múltiples hijos, pagos, clases recurrentes, disponibilidad, reprogramación y avisos.

- Integración visual del mockup sobre la base funcional, sin sustituir la lógica de agenda, pagos, reprogramación ni lista de espera.
- Corregida la estructura HTML del bloque de estilos para evitar estilos anidados inválidos.
- Portada alineada con el mockup aprobado: “Movimiento · Coordinación · Fundamentos”, “En Lima”, “Parques o domicilio”, “Coach Juan Carlos” y “ESTUDIOS FPF”.
