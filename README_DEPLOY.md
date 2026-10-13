# JC Fútbol Coach — paquete Render v12.18.14

Base: v12.15.1.

## Cambio principal
Corrige el flujo real de una clase reprogramada que luego se integra a una **Clase compartida**.

- Una clase nueva agregada a una clase compartida puede quedar vinculada a la recuperación de una clase anterior.
- La recuperación conserva el pago del período original y no se convierte en una deuda nueva.
- Si el coach agrega primero al alumno a la clase compartida y después anula la clase original, la anulación dispara la reparación de la recuperación.
- Las cancelaciones ya no destruyen la relación con el pago original; la clase cancelada no consume el pago mientras queda disponible para una recuperación.
- Se añadió una migración para `recovery_payment_id`.
- Se amplió la reparación para registros heredados en los que la clase cancelada ya había perdido su `payment_id`.
- Las recuperaciones se identifican visualmente como **Recuperación pagada** en la ficha del alumno.

## Caso de Marianito
Si existe una clase de septiembre ya pagada, se cancela y luego se agrega a una clase compartida de octubre, el sistema debe vincular la clase de octubre al pago de septiembre como recuperación, en lugar de mostrarla como "Pendiente de pago".

## Importante
La migración se ejecuta automáticamente mediante `init_db()` al iniciar la aplicación.

No se han ejecutado pruebas contra la base real de Render. Antes de desplegar, verificar específicamente:
1. Marianito: clase original septiembre + pago mensual.
2. Clase compartida con Diego en octubre.
3. Cancelación de la clase original.
4. Que la recuperación de octubre quede pagada y no consuma el pago de octubre.
5. Que no se genere una segunda deuda.


## v12.15.5 — Reparación automática al desplegar

- Al iniciar la aplicación, revisa automáticamente clases futuras ya existentes que estén pendientes de pago.
- Si encuentra una clase cancelada del período anterior con pago correspondiente, vincula la clase futura como recuperación pagada.
- Esto cubre el caso en que el alumno ya fue agregado a una Clase compartida antes de cancelar la clase original.
- No requiere volver a agregar al alumno ni registrar otro pago.
- La recuperación conserva el pago del período original y no consume el pago del mes nuevo.

Después del deploy, revisar la ficha de Marianito. No registrar un pago adicional antes de verificar el resultado.


## v12.15.6 — reparación robusta
- Repara también una clase futura que ya existe pero conserva `payment_id` con estado pendiente.
- Si la clase cancelada conserva explícitamente el pago original, esa relación tiene prioridad sobre el cálculo de capacidad.
- No aumenta `sessions_total` ni crea pagos nuevos para una recuperación.
- No crea una segunda clase: repara la fila de destino existente.
- La reparación automática sigue limitada al período inmediatamente anterior para respetar la regla mensual.


## v12.18.9 — Correcciones de pagos, clases compartidas y alumnos nuevos

- Se añadió la pantalla **Editar pago** para modificar monto, tarifa aplicada, cantidad de clases, fecha de pago y período.
- La edición del pago se realiza en una transacción y recalcula la cobertura de clases sin cambiar la tarifa habitual del alumno.
- La recalculación prioriza clases ya realizadas y conserva el vínculo de las recuperaciones con su período original.
- La sincronización de Google Calendar ahora consolida los miembros de una `session_group_id` en un solo evento, reutiliza el evento existente y elimina eventos duplicados detectados en los miembros del grupo.
- Al cancelar un miembro de una clase compartida, el evento se mantiene actualizado para los otros alumnos; se elimina únicamente cuando ya no quedan miembros activos.
- Se añadió la marca **Alumno fijo** en Editar alumno. Los registros existentes se migran como fijos para no cambiar su comportamiento; los alumnos creados a partir de esta versión empiezan como nuevos.
- Un alumno nuevo no puede marcar su primera clase como realizada si la clase no está cubierta por un pago. El entrenador puede marcarlo como fijo desde su ficha cuando corresponda.
- Al compartir una clase, si el alumno ya tiene una sola clase activa ese mismo día a otra hora, se reutiliza esa clase y se ajusta al horario compartido conservando su pago y recuperación. Si hay varias clases ese día, la aplicación no crea ni mueve ninguna y pide resolver la agenda primero.

## Despliegue en Render

1. Descomprime el ZIP y sube el contenido de `src/` al repositorio de la aplicación, conservando la estructura de carpetas.
2. Conserva los comandos de instalación e inicio ya configurados en Render. Para esta estructura, el inicio habitual es `gunicorn -c gunicorn.conf.py app:app`.
3. Conserva las variables de entorno actuales (`DATABASE_URL`, `SECRET_KEY` y las credenciales OAuth de Google si Calendar está habilitado). No sustituyas secretos por valores de ejemplo.
4. La aplicación aplica sus migraciones de esquema al iniciar mediante `init_db()`.

## Verificación local realizada para v12.18.9

- Compilación de Python correcta.
- Las 28 plantillas Jinja se cargan sin errores.
- Prueba funcional simulada de reutilización de una clase existente en otro horario del mismo día: no inserta una segunda clase y conserva la relación de pago.
- Prueba del payload de Google Calendar: el evento compartido incluye ambos alumnos y los IDs de ambas clases.
- Revisión de la ruta de edición de pagos: bloqueo transaccional, recálculo de cobertura y ausencia de actualización de la tarifa habitual del alumno.

La sincronización externa de Google Calendar depende de que las credenciales OAuth estén configuradas y autorizadas. Las pruebas locales no acceden a la base de datos ni a la cuenta de Google de producción.


## v12.18.14 — cambios adicionales

Consulta `NOTAS_VERSION_12.18.14.txt` para los cambios de confirmación de clase compartida, unión segura de reservas antiguas, persistencia de Agenda llena, validación de cantidad de clases cubierta por pagos y medición de tiempos de respuesta.
