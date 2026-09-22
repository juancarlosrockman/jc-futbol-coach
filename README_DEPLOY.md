JC Fútbol Coach — v12.9

Base: v12.8 de seguridad.

Corrección exclusiva de la portada móvil. Se conserva la portada fija, sin desplazamiento vertical, como en la referencia anterior. Para pantallas de menor altura se compactan únicamente los elementos visuales para mantener visibles los botones “¿Eres nuevo?” y “Alumno actual”.

No cambia la lógica de negocio, agenda, login, pagos, reprogramaciones ni demás funciones.

Render: mantener las variables de entorno existentes, incluida SECRET_KEY. No reemplazar DATABASE_URL.
