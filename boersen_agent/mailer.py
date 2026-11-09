"""Send generated PDF reports by e-mail (stdlib smtplib)."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from .config import Config


def send_report(cfg: Config, pdf: Path, title: str) -> None:
    if not cfg.email_to:
        raise ValueError("[email] to is empty")
    if not cfg.smtp_host or not cfg.email_from:
        raise ValueError("[email] smtp_host and from/user must be set")
    if not cfg.smtp_password:
        raise ValueError("SMTP password missing (env var named in [email] password_env)")

    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = cfg.email_from
    msg["To"] = ", ".join(cfg.email_to)
    msg.set_content(f"{title}\n\nSee the attached PDF.\n")
    msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name)

    context = ssl.create_default_context()
    if cfg.smtp_port == 465:
        server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context, timeout=60)
    else:
        server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60)
    with server:
        if cfg.smtp_port != 465:
            server.starttls(context=context)
        server.login(cfg.smtp_user or cfg.email_from, cfg.smtp_password)
        server.send_message(msg)
