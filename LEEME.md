# JC Fútbol Coach — corrección del Worker de Cloudflare

## Qué corrige este paquete
- Reemplaza la respuesta de prueba `OK - Cloudflare Python funciona`.
- Conecta la aplicación Flask existente mediante el adaptador WSGI oficial de Python Workers.
- Aplica `SECRET_KEY` al objeto Flask para que las sesiones se firmen con el secreto real.
- Obtiene `DATABASE_URL` de `HYPERDRIVE.connectionString`.
- Expone las variables OAuth de Google al código existente.
- No modifica la base de datos ni ejecuta `init_db()` al arrancar en Cloudflare.
- No incluye credenciales ni modifica la rama `main` o Render.

## Cómo aplicar
1. Descarga y descomprime este ZIP.
2. Copia `src/worker.py` sobre el archivo del mismo nombre en la raíz de tu proyecto `jc-futbol-coach` (rama `cloudflare-migration`).
3. No reemplaces `src/app.py`, `src/templates/`, `src/static/`, `pyproject.toml` ni `wrangler.jsonc`.
4. Despliega primero en el Worker de pruebas de Cloudflare, no en Render.

## Configuración requerida en Cloudflare
Deben existir estos bindings/secrets:
- `SECRET_KEY` (secreto actual de la aplicación)
- `HYPERDRIVE` (binding ya configurado hacia Supabase)
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI`

La URL de redirección de Google debe corresponder al dominio del Worker de prueba.

## Verificación tras desplegar
1. Abrir `/` y confirmar que aparece la página real, no el mensaje de prueba.
2. Probar inicio de sesión del entrenador.
3. Probar inicio de sesión de un padre.
4. Comprobar una lectura de alumnos/horarios.
5. Confirmar en logs si hay errores de CPU, conexión PostgreSQL o variables faltantes.
6. No probar escrituras que alteren datos reales hasta validar primero las lecturas.

## Limitación importante
Este paquete corrige la integración WSGI y el paso de bindings, pero no puede demostrar por sí solo que todas las rutas funcionan en la red de Cloudflare. El plan gratuito de Workers tiene un límite de CPU de 10 ms por solicitud; si el Worker vuelve a superar ese límite, se necesitará perfilar/optimizar el código o usar un plan con más CPU. El ZIP no afirma que el despliegue ya esté validado.
