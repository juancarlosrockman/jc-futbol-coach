# JC Fútbol Coach v12.15.2

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
