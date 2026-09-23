# JC Fútbol Coach v12.12

Base: v12.10 / seguridad v12.8.

Cambios:
- “Clases de hoy”: muestra la última clase realizada con anotación y permite escribir/guardar el trabajo de hoy.
- El padre puede ver únicamente el trabajo ya realizado en el historial de su(s) hijo(s).
- Portada móvil: sin scroll; ajuste responsive para pantallas cortas para mantener visibles los dos accesos principales.
- Menú del padre: hamburguesa centrada.
- Login: evita doble envío y muestra “Ingresando…” al primer toque.
- No se modifican precios, agenda, pagos ni reglas de negocio.

Render:
- Mantener DATABASE_URL y SECRET_KEY existentes.
- No requiere migración manual: work_notes ya se crea con init_db().
