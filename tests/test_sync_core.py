import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import little_hotelier_sync as lhs


def test_parse_row_exposes_room_and_rooms_for_compatibility():
    html = """
    <table><tr class="reservation_room_type">
      <td class="status"><span class="confirmed"></span></td>
      <td class="name"><span class="maskContent">Pérez, Ana</span></td>
      <td><a class="booking-reference" data-reservation-id="123" href="/frontdesk/emea/prop/reservations/uuid-1/edit">BDC-1</a></td>
      <td class="booking_source"><span>Booking.com</span></td>
      <td class="guests"><span>2</span><span>1</span><span>0</span></td>
      <td class="check_in">27-06-26</td>
      <td class="check_out">29-06-26</td>
      <td class="room_name">Habitación 1\nHabitación 2</td>
      <td class="total">100 €</td>
    </tr></table>
    """

    reservations = lhs._parse_html(html)

    assert len(reservations) == 1
    assert reservations[0]["rooms"] == ["Habitación 1", "Habitación 2"]
    assert reservations[0]["room"] == "Habitación 1, Habitación 2"


def test_sync_state_roundtrip_uses_configured_path(tmp_path, monkeypatch):
    state_path = tmp_path / "state" / "lh_sync_state.json"
    monkeypatch.setattr(lhs, "STATE_PATH", str(state_path))

    lhs.save_sync_state(last_status="ok", last_reservations_found=7)
    state = lhs.load_sync_state()

    assert state["last_status"] == "ok"
    assert state["last_reservations_found"] == 7
    assert "updated_at" in state


def test_should_run_now_runs_slot_once_in_madrid(monkeypatch):
    monkeypatch.setattr(lhs, "RUN_TIMEZONE", "Europe/Madrid")
    monkeypatch.setattr(lhs, "RUN_AT_HOURS", ["09:00", "14:00", "20:00"])
    now = datetime(2026, 6, 27, 7, 0, tzinfo=timezone.utc)  # 09:00 Madrid summer time

    should_run, slot = lhs.should_run_now(now, {})

    assert should_run is True
    assert slot == "2026-06-27T09:00"
    assert lhs.should_run_now(now, {"last_run_slot": slot}) == (False, slot)


def test_send_alert_without_provider_is_safe_and_returns_false(monkeypatch, tmp_path):
    monkeypatch.setattr(lhs, "ALERT_EMAIL_TO", "dgomezlimpatex@gmail.com")
    monkeypatch.setattr(lhs, "ALERT_EMAIL_FROM", "")
    monkeypatch.setattr(lhs, "RESEND_API_KEY", "")
    monkeypatch.setattr(lhs, "SMTP_HOST", "")
    monkeypatch.setattr(lhs, "SMTP_USER", "")
    monkeypatch.setattr(lhs, "SMTP_PASSWORD", "")
    monkeypatch.setattr(lhs, "STATE_PATH", str(tmp_path / "state.json"))

    assert lhs.send_alert("Test", "Body", force=True) is False


def test_fetch_result_can_represent_auto_login_failure():
    result = lhs.FetchResult(status=lhs.SyncStatus.AUTO_LOGIN_FAILED, message="falló login")

    assert result.status == lhs.SyncStatus.AUTO_LOGIN_FAILED
    assert result.reservations == []
    assert "login" in result.message
