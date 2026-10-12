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
