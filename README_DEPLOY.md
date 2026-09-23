# JC Fútbol Coach — versión 12.9

Base visual y funcional: versión 12.7, que era la versión comprobada antes del refuerzo de seguridad.

## Seguridad
- `SECRET_KEY` debe estar configurada en Render y no debe publicarse en GitHub.
- Las sesiones usan cookies `Secure`, `HttpOnly` y `SameSite=Lax`.
- Los formularios POST incluyen protección CSRF.
- El acceso de Coach y alumnos incorpora limitación de intentos fallidos (8 por IP en 15 minutos).
- Se agregan cabeceras de seguridad y `no-store` para pantallas autenticadas.
- El service worker no almacena HTML ni páginas con datos personales; solo recursos estáticos.
- Las contraseñas continúan almacenándose con hash mediante Werkzeug.
- Las consultas SQL continúan usando parámetros.

## Importante
Esta versión recupera la portada y los archivos visuales de la versión 12.7 y aplica únicamente el refuerzo de seguridad de la 12.8. No incorpora las correcciones visuales intermedias de 12.9 que alteraban el comportamiento de la portada.

Esta actualización es un refuerzo de seguridad a nivel de aplicación; no sustituye una auditoría profesional de infraestructura, Supabase/PostgreSQL, cuenta de Render, GitHub o dispositivos de los usuarios.
