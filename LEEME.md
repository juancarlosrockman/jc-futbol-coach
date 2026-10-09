# Corrección de archivos estáticos en Cloudflare — JC Fútbol Coach

## Qué corrige
- Publica `src/static` mediante el binding `ASSETS` de Cloudflare.
- Conserva las URLs que ya usa la app: `/static/...` y `/manifest.webmanifest`.
- Mantiene Flask/WSGI para las demás rutas.
- No modifica `src/app.py`, las plantillas ni la base de datos.

## Aplicación
1. En la rama `cloudflare-migration`, reemplaza `src/worker.py` por el incluido.
2. Actualiza `wrangler.jsonc` con el incluido (conserva el mismo Hyperdrive ID y las variables existentes).
3. Confirma que `src/static/jc_coach_photo.png` y `src/static/manifest.webmanifest` existan en esa rama.
4. Despliega la rama experimental con `uv run pywrangler deploy`.

## Pruebas obligatorias después del despliegue
- `/manifest.webmanifest` debe devolver JSON y HTTP 200.
- `/static/jc_coach_photo.png` debe devolver la imagen y HTTP 200.
- La página `/` debe mostrar la foto.
- Comprueba también login del entrenador, login de padres y carga de datos antes de considerar migración terminada.

Este ZIP contiene los dos archivos de configuración/código que se deben aplicar; no contiene una copia de la imagen original. Cloudflare publicará la imagen existente desde `src/static` al desplegar el repositorio.
