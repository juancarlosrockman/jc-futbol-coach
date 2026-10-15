"""Offline regression checks for critical safeguards in the JCFC app.

These tests intentionally validate source-level contracts without requiring
production credentials or a live PostgreSQL/Google Calendar account.
"""
from pathlib import Path
import ast
import jinja2

ROOT = Path(__file__).resolve().parents[1]


def test_python_compiles():
    for path in (ROOT / "app.py", ROOT / "gunicorn.conf.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_templates_parse():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(ROOT / "templates")))
    for path in (ROOT / "templates").glob("*.html"):
        env.get_template(path.name)


def test_shared_class_and_delete_controls_exist():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'def add_student_to_shared_class(class_id):' in source
    assert 'def delete_class(class_id):' in source
    assert 'Se eliminó esta clase del alumno. La sesión compartida del otro alumno se conservó.' in source
    assert 'if delete:' in source
    assert 'DELETE", f"/calendars/{quote(calendar_id,safe=\'\')}/events/' in source


def test_payment_delete_has_history_guards():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'def delete_payment(payment_id):' in source
    assert 'recovery_payment_id=%s LIMIT 1' in source
    assert "status='attended' LIMIT 1" in source
    assert 'c.rollback(); app.logger.exception("Payment deletion failed' in source


def test_agenda_full_is_only_reopened_by_explicit_action():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'if action == "set_full":' in source
    assert 'if action == "open":' in source
    assert 'Do not clear the manually selected "agenda llena" state' in source


def test_bulk_calendar_sync_deduplicates_shared_sessions():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'seen_groups=set(); targets=[]' in source
    assert 'if group_id in seen_groups:' in source


def test_new_booking_prompts_for_shared_session_and_can_decline():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "new_class.html").read_text(encoding="utf-8")
    assert 'def class_schedule_matches():' in source
    assert 'share_existing = form.get("share_existing", "no") == "yes"' in source
    assert '¿Deseas compartir la sesión?' in template
    assert "se agendará por separado" in template


def test_legacy_class_merge_preserves_records_and_checks_duplicates():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "edit_class.html").read_text(encoding="utf-8")
    assert 'def merge_legacy_class(class_id):' in source
    assert 'No se modificó nada.' in source
    assert 'Unir sesiones seleccionadas' in template


def test_agenda_full_uses_global_persistent_state():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "WHERE month='global' AND turn=%s" in source
    assert "VALUES('global',%s,TRUE)" in source
    assert "VALUES('global',%s,FALSE)" in source


def test_regular_payment_amount_rate_must_match_class_count():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "expected_sessions = int((amount + 1e-7) // applied_tariff)" in source
    assert "El monto y la tarifa cubren" in source


def test_bulk_future_shared_merge_route_and_ui_exist():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "edit_class.html").read_text(encoding="utf-8")
    assert 'def merge_future_shared_classes(class_id):' in source
    assert 'a.date >= %s' in source
    assert 'a.mode IS NOT DISTINCT FROM b.mode' in source
    assert 'a.place IS NOT DISTINCT FROM b.place' in source
    assert 'FOR UPDATE OF a,b' in source
    assert 'Unir clases futuras coincidentes' in template
    assert 'No se modificó nada.' in source
