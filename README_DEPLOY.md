# JC Fútbol Coach v12.14 (PREPARACIÓN PARCIAL)

Base: v12.13 adjunta.

## Incluido en esta preparación
- Recordatorios dentro de la app para padres: aparecen cuando una clase futura está a 2 horas o menos de comenzar.
- Recordatorios separados por clase/alumno y persistencia server-side al marcar “Entendido”; se mantienen vistos entre sesiones/dispositivos.
- Los recordatorios se basan en el horario actual de la clase y solo consideran estados programada/reprogramada/pospuesta, por lo que clases canceladas no se muestran.
- Tablas preparadas para guardar suscripciones push y configuración/token de Google Calendar.
- Endpoints protegidos de suscripción/desuscripción push y entrega de clave pública VAPID desde variable de entorno.

## Aún requiere completar antes de usar en producción
- Entrega de push Web Push real en segundo plano requiere integrar el emisor servidor (VAPID privado) y el despacho programado de avisos. La base y endpoints de suscripción están, pero no se debe anunciar como push funcional aún.
- Google Calendar OAuth y sincronización de creación/actualización/cancelación aún no están implementados. No configurar credenciales de Google todavía.

## Variables actuales
Mantener DATABASE_URL y SECRET_KEY existentes.
VAPID_PUBLIC_KEY solo es necesaria al completar Web Push; no es necesaria para los recordatorios dentro de la app.

## Importante
Esta versión no se ha conectado ni desplegado contra la base real de Render. Las migraciones se ejecutan por init_db() al iniciar la app, como en versiones anteriores. Probar primero en un entorno/base de datos de pruebas.


## Google Calendar (v12.14)
After deploying, configure these Render environment variables:
- GOOGLE_CLIENT_ID
- GOOGLE_CLIENT_SECRET
- GOOGLE_REDIRECT_URI = https://jc-futbol-coach.onrender.com/oauth/google/callback

In Google Cloud, enable Google Calendar API, configure OAuth consent, and register the redirect URI above in the OAuth Web application client. Then sign into the Coach account, open `/entrenador/google-calendar`, and select “Conectar con Google”. The app creates a dedicated calendar named “JC Fútbol Coach – Clases” and backfills currently scheduled/rescheduled/postponed classes.

Access and refresh tokens are encrypted in PostgreSQL using a key derived from the existing SECRET_KEY. Keep SECRET_KEY stable; changing it will require reconnecting Google. Calendar events are created/updated on class create/edit/reschedule and deleted on cancellation or deletion. The app uses a 60-minute event duration, with America/Lima timezone. Existing personal calendar events are not edited.
