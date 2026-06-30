#!/usr/bin/env python3
"""MediaRunner completion alerts for email and Google Chat."""
from __future__ import annotations

import json
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from typing import Callable


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _split_addresses(value: str) -> list[str]:
    text = str(value or "").replace(";", ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def _status_bucket(status: str) -> str:
    text = str(status or "").strip().lower()
    if "cancel" in text or "stop" in text:
        return "cancelled"
    if text in {"complete", "completed", "verified", "ok", "success"} or "complete" in text:
        return "success"
    if text == "test":
        return "test"
    return "failure"


def should_send_alert(cfg: dict, status: str) -> bool:
    bucket = _status_bucket(status)
    if bucket == "test":
        return True
    if bucket == "success":
        return _as_bool(cfg.get("alerts_notify_success"), True)
    if bucket == "cancelled":
        return _as_bool(cfg.get("alerts_notify_cancelled"), True)
    return _as_bool(cfg.get("alerts_notify_failure"), True)


def _summary_lines(summary: dict) -> list[str]:
    lines = ["MediaRunner transfer alert"]
    fields = [
        ("Status", summary.get("status")),
        ("Workflow", summary.get("workflow")),
        ("Job", summary.get("job")),
        ("Source", summary.get("source")),
        ("Destinations", summary.get("destinations")),
        ("Progress", summary.get("progress")),
        ("Files", summary.get("files")),
        ("Bytes", summary.get("bytes")),
    ]
    for label, value in fields:
        text = str(value or "").strip()
        if text:
            lines.append(f"{label}: {text}")
    reports = [str(item).strip() for item in (summary.get("reports") or []) if str(item).strip()]
    manifests = [str(item).strip() for item in (summary.get("manifests") or []) if str(item).strip()]
    if reports:
        lines.append("Reports:")
        lines.extend(f"- {item}" for item in reports[:8])
    if manifests:
        lines.append("Manifests:")
        lines.extend(f"- {item}" for item in manifests[:8])
    note = str(summary.get("note") or "").strip()
    if note:
        lines.append(f"Note: {note}")
    return lines


def format_alert_text(summary: dict) -> str:
    return "\n".join(_summary_lines(summary))


def _email_subject(cfg: dict, summary: dict) -> str:
    prefix = str(cfg.get("alerts_email_subject_prefix") or "MediaRunner").strip() or "MediaRunner"
    status = str(summary.get("status") or "Alert").strip() or "Alert"
    job = str(summary.get("job") or summary.get("workflow") or "").strip()
    return f"{prefix}: {status}" + (f" - {job}" if job else "")


def send_email_alert(cfg: dict, summary: dict, *, timeout: float = 10.0) -> None:
    host = str(cfg.get("alerts_smtp_host") or "").strip()
    port = int(cfg.get("alerts_smtp_port") or 587)
    security = str(cfg.get("alerts_smtp_security") or "STARTTLS").strip().upper()
    username = str(cfg.get("alerts_smtp_username") or "").strip()
    password = str(cfg.get("alerts_smtp_password") or "")
    from_addr = str(cfg.get("alerts_email_from") or username or "").strip()
    recipients = _split_addresses(str(cfg.get("alerts_email_to") or ""))
    if not host:
        raise ValueError("SMTP host is required")
    if not from_addr:
        raise ValueError("Email From address is required")
    if not recipients:
        raise ValueError("At least one Email To address is required")

    message = EmailMessage()
    message["Subject"] = _email_subject(cfg, summary)
    message["From"] = from_addr
    message["To"] = ", ".join(recipients)
    message.set_content(format_alert_text(summary))

    if security in {"SSL/TLS", "SSL", "TLS"}:
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context()) as smtp:
            if username or password:
                smtp.login(username, password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
        smtp.ehlo()
        if security in {"STARTTLS", "START TLS"}:
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if username or password:
            smtp.login(username, password)
        smtp.send_message(message)


def send_google_chat_alert(cfg: dict, summary: dict, *, timeout: float = 10.0) -> None:
    webhook = str(cfg.get("alerts_gchat_webhook_url") or "").strip()
    if not webhook:
        raise ValueError("Google Chat webhook URL is required")
    payload = json.dumps({"text": format_alert_text(summary)}).encode("utf-8")
    request = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if int(getattr(response, "status", 200)) >= 400:
            raise RuntimeError(f"Google Chat webhook returned HTTP {response.status}")


def send_alerts(cfg: dict, summary: dict, *, force: bool = False, timeout: float = 10.0) -> list[dict[str, str]]:
    if not force and not should_send_alert(cfg, str(summary.get("status") or "")):
        return []
    results: list[dict[str, str]] = []
    providers: list[tuple[str, Callable]] = []
    if _as_bool(cfg.get("alerts_email_enabled"), False):
        providers.append(("Email", send_email_alert))
    if _as_bool(cfg.get("alerts_gchat_enabled"), False):
        providers.append(("Google Chat", send_google_chat_alert))
    for label, sender in providers:
        try:
            sender(cfg, summary, timeout=timeout)
            results.append({"provider": label, "status": "sent", "message": ""})
        except Exception as exc:
            results.append({"provider": label, "status": "failed", "message": str(exc)})
    return results


def send_test_alerts(cfg: dict, *, timeout: float = 10.0) -> list[dict[str, str]]:
    summary = {
        "status": "Test",
        "workflow": "Alerts",
        "job": "MediaRunner alert test",
        "source": "Settings",
        "destinations": "Configured alert providers",
        "progress": "This is a test notification.",
    }
    return send_alerts(cfg, summary, force=True, timeout=timeout)
