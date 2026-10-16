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
    assert "status IN ('attended','absent') LIMIT 1" in source
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


def test_attendance_has_attended_and_absent_states():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    dashboard = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
    detail = (ROOT / "templates" / "student_detail.html").read_text(encoding="utf-8")
    assert '"absent": "No asistió"' in source
    assert '"attended", "absent"' in source
    assert 'value="absent"' in dashboard
    assert 'value="absent"' in detail
    assert "status IN ('attended','absent')" in source


def test_parent_rebooking_only_shows_rules_and_whatsapp_no_self_booking():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "reschedule.html").read_text(encoding="utf-8")
    route = source.split('@app.route("/alumno/clase/<int:class_id>/reprogramar"', 1)[1].split('@app.route("/entrenador/clase/<int:class_id>/reprogramar"', 1)[0]
    assert "whatsapp_url = f\"https://wa.me/{COACH_WHATSAPP}?text={quote(message)}\"" in route
    assert "return render_template(\"reschedule.html\", current=current, slots=[], coach_mode=False" in route
    assert "c.execute(\"UPDATE classes" not in route
    assert "INSERT INTO coach_notifications" not in route
    assert "primera reprogramación</b> pasa al <b>mes siguiente" in template
    assert "A partir de la segunda reprogramación" in template
    assert "la clase siempre se recupera" in template
    assert "no puede volver a reprogramarse" in template
    assert "Coordinar con Coach Juan Carlos por WhatsApp" in template
    assert "Confirmar nueva fecha" not in template


def test_attendance_selectors_expose_exactly_four_states():
    for name in ("dashboard.html", "student_detail.html"):
        source = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert 'option value="scheduled"' in source
        assert 'option value="attended"' in source
        assert 'option value="absent"' in source
        assert 'option value="rescheduled"' in source
        assert 'option value="postponed"' not in source
        assert 'option value="cancelled"' not in source
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'allowed={"scheduled","attended","absent","rescheduled"}' in app


def test_whatsapp_coach_number_is_configured():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'COACH_WHATSAPP = "51993757225"' in source


def test_edit_class_merge_candidate_query_casts_optional_session_group_parameters():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "CAST(%s AS text) IS NULL OR CAST(cl.session_group_id AS text) IS DISTINCT FROM CAST(%s AS text)" in source


def test_google_oauth_refresh_errors_are_diagnosable_without_logging_secrets():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "Google OAuth token refresh rejected: http_status=%s provider_error=%s" in source
    assert 'provider_error == "invalid_grant"' in source
    assert 'provider_error == "invalid_client"' in source
    assert "client_secret" not in source.split('app.logger.error("Google OAuth token refresh rejected:', 1)[1].split("if provider_error", 1)[0]
