"""Deliver nudges: native desktop notifications on every OS, plus optional phone push.

* macOS   — ``osascript`` (built in)
* Windows — a WinRT toast via PowerShell (built in on Windows 10/11)
* Linux   — ``notify-send`` (libnotify)
* Android — ``termux-notification`` when running under Termux
* Phone   — any ntfy topic URL (ntfy.sh or self-hosted); only the nudge text is sent
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import urllib.request
from xml.sax.saxutils import escape

from ..platforms import ANDROID, MACOS, WINDOWS, os_name

log = logging.getLogger(__name__)
POWERSHELL_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def _run(cmd: list[str]) -> bool:
    kwargs = {"timeout": 10, "capture_output": True}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        return subprocess.run(cmd, **kwargs).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def windows_toast_script(title: str, body: str) -> str:
    def ps(s: str) -> str:  # XML-escape, then make safe inside a PowerShell single-quoted string
        return escape(s, {'"': "&quot;"}).replace("'", "''")
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{ps(title)}</text><text>{ps(body)}</text></binding></visual></toast>');"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{POWERSHELL_APP_ID}')"
        ".Show((New-Object Windows.UI.Notifications.ToastNotification $x))"
    )


def desktop(title: str, body: str) -> bool:
    osn = os_name()
    if osn == MACOS:
        script = f"display notification {json.dumps(body, ensure_ascii=False)} with title {json.dumps(title, ensure_ascii=False)}"
        return _run(["osascript", "-e", script])
    if osn == WINDOWS:
        return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", windows_toast_script(title, body)])
    if osn == ANDROID and shutil.which("termux-notification"):
        return _run(["termux-notification", "--title", title, "--content", body, "--group", "zenchan"])
    if shutil.which("notify-send"):
        return _run(["notify-send", "-a", "Zen-chan", title, body])
    try:
        from plyer import notification  # optional
        notification.notify(title=title, message=body, app_name="Zen-chan", timeout=8)
        return True
    except Exception:
        return False


def ntfy(url: str, title: str, body: str) -> bool:
    """Push to a phone via ntfy. ``url`` is a topic URL, e.g. https://ntfy.sh/my-secret-topic."""
    if not url:
        return False
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST",
                                 headers={"Title": title.encode("ascii", "ignore").decode().strip() or "Zen-chan",
                                          "Tags": "eyes"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except OSError as exc:
        log.debug("ntfy failed: %s", exc)
        return False


def deliver(zen, nudge: dict) -> dict:
    cfg = zen.settings["notify"]
    result = {"desktop": False, "ntfy": False}
    if cfg.get("desktop", True):
        result["desktop"] = desktop(nudge["title"], nudge["body"])
    if cfg.get("ntfy_url"):
        result["ntfy"] = ntfy(cfg["ntfy_url"], nudge["title"], nudge["body"])
    if nudge.get("id"):
        zen.db.x("UPDATE nudges SET delivered=? WHERE id=?", (1 if any(result.values()) else 0, nudge["id"]))
    return result
