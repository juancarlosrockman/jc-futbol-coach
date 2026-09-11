# JC Fútbol Coach

Versión web funcional para entrenamiento personalizado de fútbol.

## Base de datos y Render
La aplicación usa PostgreSQL mediante `DATABASE_URL`. En Render deben estar configuradas las variables de entorno necesarias para la aplicación, incluyendo `DATABASE_URL`, `SECRET_KEY`, `COACH_USER` y `COACH_PASSWORD`.

## Flujo de alumno nuevo
1. El interesado entra a “¿Eres nuevo? Solicita tu entrenamiento”.
2. Completa los datos básicos y el horario de interés.
3. Al enviar, se abre WhatsApp con un mensaje preparado para JC Fútbol Coach.
4. La ficha completa del alumno se registra manualmente desde el panel del entrenador.
5. El entrenador asigna manualmente la tarifa correspondiente y entrega al padre/madre sus credenciales.

## Disponibilidad
Los horarios publicados se guardan en PostgreSQL y pueden agregarse o eliminarse desde el panel del entrenador. Los horarios publicados se muestran en el formulario de alumno nuevo.

## Tarifas públicas
- 3 años: 30 min — S/ 50
- 4–5 años: 45 min — S/ 60
- Desde 6 años hasta adultos: 1 hora — S/ 70
- Plan 4 sesiones: S/ 280
- Plan 8 sesiones: S/ 520

Las tarifas de cada alumno se mantienen manuales desde su ficha.
