JC FUTBOL COACH — CORRECCIÓN DE CONTROLADOR POSTGRESQL PARA CLOUDFLARE

Incluye:
- pyproject.toml con psycopg[binary], para incluir el wrapper PostgreSQL que faltaba (psycopg_binary).
- src/worker.py conservando la corrección de SECRET_KEY y el servicio de archivos estáticos.
- wrangler.jsonc con fecha de compatibilidad 2026-10-09 y la configuración existente de Hyperdrive y ASSETS.

IMPORTANTE PARA QUE FUNCIONE:
Este paquete debe aplicarse al PROYECTO COMPLETO y desplegarse con el proceso de construcción de Python Workers (pywrangler/uv), que instala las dependencias declaradas en pyproject.toml. Subir solo src/worker.py al editor web no instala psycopg[binary] y no corrige el error `no pq wrapper available`.

Comprobaciones posteriores al despliegue:
1. Abrir la página principal y verificar la foto.
2. Intentar iniciar sesión como coach.
3. Si falla, revisar el evento más reciente en Cloudflare Observability.

No elimina ni modifica tablas o datos de PostgreSQL.
