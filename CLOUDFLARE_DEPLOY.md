# JC Fútbol Coach — Cloudflare Workers

Esta carpeta contiene la versión preparada para la migración de Render a Cloudflare Python Workers.

## Estructura
- `src/app.py` — aplicación Flask original.
- `src/templates/` — plantillas originales.
- `src/static/` — archivos estáticos originales.
- `src/worker.py` — entrada WSGI para Cloudflare.
- `pyproject.toml` — dependencias compatibles con Pywrangler.
- `wrangler.jsonc` — Worker + Hyperdrive.

## Importante
- No contiene secretos ni contraseñas.
- `DATABASE_URL` no se configura: se obtiene de `HYPERDRIVE.connectionString`.
- `keep_vars` evita que un despliegue desde Git reemplace las variables/secrets configurados en Cloudflare.
- El Hyperdrive configurado apunta a la base existente de Supabase.
- Render no se modifica con estos archivos.

## Despliegue
En Cloudflare Workers:
1. Rama de producción: `cloudflare-migration`.
2. Deploy command: `uv run pywrangler deploy`.
3. Después del despliegue, revisar primero `/` y luego el login.

La aplicación importa `app.py` después de inyectar las variables de Cloudflare. Esto es importante porque la aplicación valida `SECRET_KEY` y ejecuta `init_db()` al importar.
