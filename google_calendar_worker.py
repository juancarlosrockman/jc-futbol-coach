"""Procesador interno de la cola durable de Google Calendar.

Se ejecuta como hilo daemon dentro del propio servicio web de JCFC.
No requiere Render Cron Job ni un segundo servicio.
La cola vive en PostgreSQL, por lo que las tareas sobreviven a reinicios.
"""
import logging
import threading
import time

from app import db, sync_class_to_google

logger = logging.getLogger("jcfc.calendar_worker")
BATCH_SIZE = 10
LOCK_SECONDS = 300
POLL_SECONDS = 8


def claim_batch():
    c = db()
    try:
        rows = c.execute("""
            WITH due AS (
                SELECT class_id
                FROM google_calendar_sync_queue
                WHERE next_attempt_at <= CURRENT_TIMESTAMP
                  AND (locked_until IS NULL OR locked_until < CURRENT_TIMESTAMP)
                ORDER BY created_at, class_id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            UPDATE google_calendar_sync_queue q
            SET locked_until = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                updated_at = CURRENT_TIMESTAMP
            FROM due
            WHERE q.class_id = due.class_id
            RETURNING q.class_id, q.attempts
        """, (BATCH_SIZE, LOCK_SECONDS)).fetchall()
        c.commit()
        return rows
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def record_result(class_id, success, error, previous_attempts):
    c = db()
    try:
        if success:
            c.execute("DELETE FROM google_calendar_sync_queue WHERE class_id=%s", (class_id,))
        else:
            attempts = int(previous_attempts or 0) + 1
            message = str(error or "Error de sincronización")[:1000]
            low = message.lower()
            if any(marker in low for marker in ("invalid_grant", "invalid_client", "reconecta", "no está autorizado", "no esta autorizado")):
                delay = min(21600, 900 * attempts)
            else:
                delay = min(3600, 60 * (2 ** min(attempts - 1, 5)))
            c.execute("""UPDATE google_calendar_sync_queue
                SET attempts=%s, last_error=%s,
                    next_attempt_at=CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    locked_until=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE class_id=%s""", (attempts, message, delay, class_id))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def run_once():
    tasks = claim_batch()
    auth_error = None
    for task in tasks:
        class_id = task["class_id"]
        if auth_error:
            # One rejected OAuth refresh applies to every queued class. Avoid
            # hammering Google's token endpoint once per class in this batch.
            record_result(class_id, False, auth_error, task["attempts"])
            continue
        try:
            success, error = sync_class_to_google(class_id, dedupe_calendar=True)
        except Exception as exc:
            success, error = False, str(exc)
            logger.exception("Fallo inesperado al sincronizar clase %s", class_id)
        record_result(class_id, success, error, task["attempts"])
        if not success and any(marker in str(error or "").lower() for marker in ("invalid_grant", "invalid_client", "google rechazó la autorización", "google rechazó las credenciales")):
            auth_error = error
    return len(tasks)


def _worker_loop():
    logger.info("Procesador interno de Google Calendar iniciado")
    while True:
        try:
            processed = run_once()
            # If work was found, immediately check for the next batch.
            if processed:
                continue
        except Exception:
            logger.exception("Error en el procesador interno de Google Calendar")
        time.sleep(POLL_SECONDS)


_started = False
_start_lock = threading.Lock()


def start_daemon():
    """Start one background worker per web process; PostgreSQL locks coordinate workers."""
    global _started
    with _start_lock:
        if _started:
            return False
        thread = threading.Thread(target=_worker_loop, name="jcfc-google-calendar", daemon=True)
        thread.start()
        _started = True
        return True


if __name__ == "__main__":
    # Optional manual execution for local diagnostics; Render needs no separate job.
    while True:
        try:
            if not run_once():
                time.sleep(POLL_SECONDS)
        except Exception:
            logger.exception("Error en el procesador interno de Google Calendar")
            time.sleep(POLL_SECONDS)
