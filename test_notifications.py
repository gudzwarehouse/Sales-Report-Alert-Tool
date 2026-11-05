"""Unit tests for notifications.py's recipient-resolution logic -- no real
SMTP calls (send_alert's smtplib.SMTP call is left to fail/log, never
raise; these tests only check who it WOULD send to by inspecting the
built EmailMessage via a monkeypatched smtplib.SMTP).
"""

from __future__ import annotations

import notifications


class _FakeSMTP:
    sent_messages: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, *args):
        pass

    def send_message(self, message):
        _FakeSMTP.sent_messages.append(message)


def _capture_sent(monkeypatch):
    _FakeSMTP.sent_messages = []
    monkeypatch.setattr(notifications.smtplib, "SMTP", _FakeSMTP)
    return _FakeSMTP.sent_messages


def _set_env(monkeypatch):
    monkeypatch.setenv("NOTIFY_EMAIL_FROM", "sender@example.com")
    monkeypatch.setenv("NOTIFY_EMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "default-to@example.com")
    monkeypatch.setenv("NOTIFY_EMAIL_CC", "default-cc@example.com")
    monkeypatch.setattr(notifications, "load_dotenv", lambda *a, **k: None)


def test_omitted_to_and_cc_fall_back_to_env_defaults(monkeypatch):
    sent = _capture_sent(monkeypatch)
    _set_env(monkeypatch)

    notifications.send_alert("Subject", "Body")

    assert len(sent) == 1
    assert sent[0]["To"] == "default-to@example.com"
    assert sent[0]["Cc"] == "default-cc@example.com"


def test_explicit_empty_cc_list_is_respected_not_defaulted(monkeypatch):
    """Regression test: cc=[] must mean "no CC", not "use NOTIFY_EMAIL_CC" --
    a real send during manual testing accidentally CC'd a real recipient
    because cc=[] used to fall through to the .env default."""
    sent = _capture_sent(monkeypatch)
    _set_env(monkeypatch)

    notifications.send_alert("Subject", "Body", to=["someone@example.com"], cc=[])

    assert len(sent) == 1
    assert sent[0]["To"] == "someone@example.com"
    assert sent[0]["Cc"] is None


def test_explicit_to_overrides_default_and_cc_still_defaults(monkeypatch):
    sent = _capture_sent(monkeypatch)
    _set_env(monkeypatch)

    notifications.send_alert("Subject", "Body", to=["someone@example.com"])

    assert sent[0]["To"] == "someone@example.com"
    assert sent[0]["Cc"] == "default-cc@example.com"


def test_no_recipients_at_all_does_not_send(monkeypatch):
    sent = _capture_sent(monkeypatch)
    monkeypatch.setenv("NOTIFY_EMAIL_FROM", "sender@example.com")
    monkeypatch.setenv("NOTIFY_EMAIL_APP_PASSWORD", "app-password")
    monkeypatch.delenv("NOTIFY_EMAIL_TO", raising=False)
    monkeypatch.delenv("NOTIFY_EMAIL_CC", raising=False)
    monkeypatch.setattr(notifications, "load_dotenv", lambda *a, **k: None)

    notifications.send_alert("Subject", "Body")

    assert sent == []
