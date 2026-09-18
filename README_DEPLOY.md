# JC Fútbol Coach — versión para GitHub + Render

Esta carpeta contiene la aplicación Flask completa. **El contenido de esta carpeta debe quedar en la raíz del repositorio de GitHub**, no dentro de otra carpeta `app/`.

## Estructura correcta
- `app.py`
- `templates/`
- `static/`
- `requirements.txt`
- `gunicorn.conf.py`
- `mockup/`

## Render
Build Command:
`pip install -r requirements.txt`

Start Command:
`gunicorn app:app`

Variables de entorno obligatorias:
- `DATABASE_URL`
- `SECRET_KEY`
- `COACH_USER`
- `COACH_PASSWORD`

No subir `.env` ni credenciales a GitHub.
