"""The dashboard server (desktop + phone, installable as a PWA).

Security model — this server holds your entire browsing history:
* binds to 127.0.0.1 unless you pass ``--lan``;
* any non-loopback client (your phone, the extension on another device) needs
  the pairing token (header ``X-Zen-Token``, or ``?token=`` once, then a cookie);
* loopback clients must use a loopback Host name (defeats DNS rebinding);
* state-changing requests need the ``X-Zen: 1`` header or the token, which
  cross-site forms/images cannot send (defeats CSRF);
* CORS is only ever granted to browser-extension origins.
"""

from __future__ import annotations

import hmac
import json
import re
import shutil
import socket
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory, stream_with_context

from .. import __version__, ingest, platforms
from ..analysis import engine
from ..analysis.embed import available_backends
from ..analysis.sessions import train_regret
from ..buddy import narrator, nudges
from ..buddy.events import BUS
from ..browsers import LOCAL_DEVICE
from ..core import Zen

HERE = Path(__file__).parent
LOOPBACK_ADDRS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
EXTENSION_ORIGIN = re.compile(r"^(chrome|moz|safari-web|ms-browser)-extension://[\w.-]+$")
SETTINGS_KEYS = {"goals", "nudges", "notify", "narrator", "timezone", "device_aliases", "mirror_deletions",
                 "ml", "watch_interval", "disabled_sources", "discover_system"}


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no packet is sent; just picks the outbound interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Jobs:
    """One refresh at a time; progress is broadcast on the event bus."""

    def __init__(self, zen: Zen):
        self.zen = zen
        self.lock = threading.Lock()
        self.running = False

    def refresh(self, full: bool = False) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
        threading.Thread(target=self._run, args=(full,), daemon=True, name="zenchan-refresh").start()
        return True

    def _run(self, full: bool) -> None:
        emit = lambda line: BUS.publish({"type": "progress", "line": line})
        try:
            emit("> syncing browsers")
            report = ingest.sync(self.zen, progress=emit, full=full, prune=True)
            emit(f"> +{report['new_visits']} new visits, {report['removed']} forgotten")
            engine.analyze(self.zen, progress=emit)
            BUS.publish({"type": "progress", "line": "> done", "done": True})
        except Exception as exc:  # surface errors in the UI terminal instead of dying silently
            BUS.publish({"type": "progress", "line": f"> error: {type(exc).__name__}: {exc}", "done": True, "error": True})
        finally:
            with self.lock:
                self.running = False


def create_app(zen: Zen | None = None, watcher=None, lan: bool = False) -> Flask:
    zen = zen or Zen()
    app = Flask(__name__, template_folder=str(HERE / "templates"), static_folder=str(HERE / "static"),
                static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024
    app.config["zen"] = zen
    jobs = Jobs(zen)
    app.config["jobs"] = jobs

    def token_ok() -> bool:
        supplied = request.headers.get("X-Zen-Token") or request.args.get("token") or request.cookies.get("zen_token") or ""
        return bool(supplied) and hmac.compare_digest(supplied, zen.settings.token)

    def is_loopback() -> bool:
        return (request.remote_addr or "") in LOOPBACK_ADDRS

    def cors_origin() -> str | None:
        origin = request.headers.get("Origin", "")
        return origin if EXTENSION_ORIGIN.match(origin) else None

    @app.before_request
    def guard():
        if request.method == "OPTIONS":
            return ("", 204) if cors_origin() else abort(403)
        host = request.host.split("]")[0] + "]" if request.host.startswith("[") else request.host.rsplit(":", 1)[0]
        authed = token_ok()
        if is_loopback():
            if host.lower() not in LOOPBACK_HOSTS and not authed:
                abort(403, "Unexpected Host header (DNS-rebinding protection). Use http://127.0.0.1")
        elif not authed:
            if request.path == "/" or request.path.startswith("/static/") or request.path in ("/sw.js", "/manifest.webmanifest"):
                if request.path == "/":
                    return render_template("pair.html"), 401
                return None
            abort(401)
        if request.method not in ("GET", "HEAD") and request.headers.get("X-Zen") != "1" and not authed:
            abort(403, "Missing X-Zen header")
        return None

    @app.after_request
    def headers(resp):
        if request.args.get("token") and token_ok():
            resp.set_cookie("zen_token", zen.settings.token, max_age=365 * 86400, httponly=True, samesite="Strict")
        origin = cors_origin()
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Zen-Token, X-Zen"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Vary"] = "Origin"
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
                                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'")
        return resp

    # --- pages -------------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html", version=__version__)

    @app.get("/sw.js")
    def service_worker():
        resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")

    # --- read APIs ---------------------------------------------------------------
    @app.get("/api/status")
    def status():
        db = zen.db
        sources = [dict(r) for r in db.q("SELECT id, browser, engine, profile, name, origin, status, detail, "
                                         "visit_count, last_sync, seen FROM sources ORDER BY visit_count DESC")]
        disabled = set(zen.settings.get("disabled_sources", []))
        for s in sources:
            s["enabled"] = s["id"] not in disabled
        devices = [dict(r) for r in db.q("SELECT device, COUNT(*) visits, MAX(ts) last FROM visits GROUP BY device")]
        aliases = zen.settings.get("device_aliases", {})
        for d in devices:
            d["label"] = engine.device_label(d["device"], aliases)
        return jsonify({
            "version": __version__, "platform": platforms.describe(),
            "visits": db.scalar("SELECT COUNT(*) FROM visits", default=0),
            "last_analysis": db.get_meta("last_analysis"), "sources": sources, "devices": devices,
            "ml": available_backends(), "watcher": bool(watcher and watcher.thread and watcher.thread.is_alive()),
            "refreshing": jobs.running, "lan": lan, "regret_model": db.get_meta("regret_model"),
            "unseen_nudges": db.scalar("SELECT COUNT(*) FROM nudges WHERE seen=0", default=0),
        })

    @app.get("/api/insights")
    def insights():
        window = request.args.get("window", "7d")
        if window not in engine.WINDOWS:
            abort(400, "unknown window")
        doc = zen.db.get_insight(window)
        if doc is None:
            return jsonify({"window": window, "pending": True})
        return jsonify(doc)

    @app.get("/api/now")
    def now():
        return jsonify(nudges.public(nudges.now_status(zen)))

    @app.get("/api/map")
    def semantic_map():
        dim = request.args.get("dim", "2d")
        window = request.args.get("window", "all")
        start, _ = engine.window_bounds(window if window in engine.WINDOWS else "all", zen.tz)
        limit = int(zen.settings["ml"].get("map_points", 4000))
        rows = zen.db.q("""SELECT t.key, t.title, t.domain, t.topic, t.x2, t.y2, t.x3, t.y3, t.z3,
                                  SUM(v.duration) dur, MAX(v.category) cat
                           FROM titles t JOIN visits v ON v.title_key = t.key
                           WHERE v.ts >= ? AND t.x2 IS NOT NULL
                           GROUP BY t.key ORDER BY dur DESC LIMIT ?""", (start, limit))
        topics = {r["id"]: r["label"] for r in zen.db.q("SELECT id, label FROM topics")}
        from ..knowledge import color_of
        pts = [{"title": r["title"], "domain": r["domain"], "topic": topics.get(r["topic"], ""),
                "category": r["cat"], "color": color_of(r["cat"] or "Other"),
                "minutes": round((r["dur"] or 0) / 60, 1),
                "x": r["x2"] if dim == "2d" else r["x3"], "y": r["y2"] if dim == "2d" else r["y3"],
                "z": None if dim == "2d" else r["z3"]} for r in rows]
        return jsonify({"points": pts, "embedder": (zen.db.get_meta("last_analysis") or {}).get("embedder")})

    @app.get("/api/nudges")
    def list_nudges():
        since = float(request.args.get("since", 0) or 0)
        return jsonify(nudges.recent(zen.db, since))

    @app.get("/api/stream")
    def stream():
        q = BUS.subscribe()

        @stream_with_context
        def gen():
            try:
                yield f"data: {json.dumps({'type': 'hello', 'refreshing': jobs.running})}\n\n"
                last_ping = time.time()
                while True:
                    try:
                        event = q.get(timeout=5)
                        yield f"data: {json.dumps(event, default=str)}\n\n"
                    except Exception:
                        pass
                    if time.time() - last_ping > 15:
                        last_ping = time.time()
                        yield ": ping\n\n"
            finally:
                BUS.unsubscribe(q)

        return Response(gen(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/buddy/narrate")
    def narrate():
        return jsonify(narrator.narrate(zen, request.args.get("window", "today")))

    @app.get("/api/settings")
    def get_settings():
        return jsonify(zen.settings.public())

    @app.get("/api/pair")
    def pair():
        if not is_loopback():
            abort(403)
        ip = lan_ip()
        port = request.host.rsplit(":", 1)[-1] if ":" in request.host else "7777"
        return jsonify({"lan": lan, "url": f"http://{ip}:{port}/?token={zen.settings.token}",
                        "server": f"http://{ip}:{port}", "token": zen.settings.token})

    @app.get("/api/pair/qr.svg")
    def pair_qr():
        """QR code for pairing a phone, rendered server-side (no third-party scripts)."""
        if not is_loopback():
            abort(403)
        try:
            import io

            import segno
        except ImportError:
            abort(404, "pip install segno to show a QR code")
        port = request.host.rsplit(":", 1)[-1] if ":" in request.host else "7777"
        qr = segno.make(f"http://{lan_ip()}:{port}/?token={zen.settings.token}", error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="svg", scale=5, border=2, dark="#000", light="#fff")
        return Response(buf.getvalue(), mimetype="image/svg+xml", headers={"Cache-Control": "no-store"})

    @app.get("/api/export")
    def export():
        payload = {"exported": time.time(), "version": __version__,
                   "insights": {w: zen.db.get_insight(w) for w in engine.WINDOWS},
                   "settings": zen.settings.public(),
                   "nudges": nudges.recent(zen.db, 0, 1000)}
        return Response(json.dumps(payload, indent=2, default=str), mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=zenchan-export.json"})

    # --- write APIs --------------------------------------------------------------
    @app.post("/api/refresh")
    def refresh():
        started = jobs.refresh(full=bool((request.get_json(silent=True) or {}).get("full")))
        return jsonify({"started": started, "running": True})

    @app.post("/api/settings")
    def set_settings():
        patch = request.get_json(force=True, silent=True) or {}
        clean = {k: v for k, v in patch.items() if k in SETTINGS_KEYS}
        if "narrator" in clean:
            clean["narrator"] = {k: v for k, v in clean["narrator"].items()
                                 if k in ("backend", "ollama_url", "ollama_model", "anthropic_model", "share_titles")}
            if clean["narrator"].get("backend") not in (None, "local", "ollama", "anthropic"):
                abort(400, "unknown narrator backend")
        zen.settings.update(clean)
        return jsonify(zen.settings.public())

    @app.post("/api/sources/<path:source_id>")
    def toggle_source(source_id):
        enabled = bool((request.get_json(force=True, silent=True) or {}).get("enabled", True))
        disabled = set(zen.settings.get("disabled_sources", []))
        (disabled.discard if enabled else disabled.add)(source_id)
        zen.settings.update({"disabled_sources": sorted(disabled)})
        return jsonify({"id": source_id, "enabled": enabled})

    @app.post("/api/devices/<path:device>")
    def alias_device(device):
        alias = str((request.get_json(force=True, silent=True) or {}).get("alias", "")).strip()[:60]
        aliases = dict(zen.settings.get("device_aliases", {}))
        if alias:
            aliases[device] = alias
        else:
            aliases.pop(device, None)
        zen.settings.data["device_aliases"] = aliases
        zen.settings.save()
        return jsonify({"device": device, "alias": alias})

    @app.post("/api/nudges/<int:nudge_id>/seen")
    def seen(nudge_id):
        zen.db.x("UPDATE nudges SET seen=1 WHERE id=?", (nudge_id,))
        return jsonify({"ok": True})

    @app.post("/api/nudges/seen")
    def seen_all():
        zen.db.x("UPDATE nudges SET seen=1 WHERE seen=0")
        return jsonify({"ok": True})

    @app.post("/api/sessions/<int:session_id>/label")
    def label_session(session_id):
        label = (request.get_json(force=True, silent=True) or {}).get("label")
        if label not in ("good", "regret", None):
            abort(400, "label must be good, regret or null")
        row = zen.db.one("SELECT device, start, features FROM sessions WHERE id=?", (session_id,))
        if not row:
            abort(404)
        if label is None:
            zen.db.x("DELETE FROM session_labels WHERE device=? AND start=?", (row["device"], round(row["start"], 3)))
        else:
            zen.db.x("""INSERT OR REPLACE INTO session_labels(device, start, label, features, created)
                        VALUES (?,?,?,?,?)""", (row["device"], round(row["start"], 3), label, row["features"], time.time()))
        zen.db.x("UPDATE sessions SET label=? WHERE id=?", (label, session_id))
        model = train_regret(zen.db)
        n = zen.db.scalar("SELECT COUNT(*) FROM session_labels", default=0)
        return jsonify({"id": session_id, "label": label, "labels": n, "model": model})

    @app.post("/api/buddy/chat")
    def chat():
        body = request.get_json(force=True, silent=True) or {}
        message = str(body.get("message", ""))[:1000]
        return jsonify(narrator.chat(zen, message, body.get("window")))

    @app.post("/api/import")
    def import_file():
        f = request.files.get("file")
        if not f or not f.filename:
            abort(400, "no file")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(f.filename).name)[:80]
        if Path(name).suffix.lower() not in (".json", ".csv", ".zip"):
            abort(400, "upload a .json, .csv or .zip export")
        dest = zen.imports_dir / name
        f.save(dest)
        jobs.refresh()
        return jsonify({"saved": name})

    @app.post("/api/events")
    def events():
        """Foreground-time spans from the browser extension."""
        body = request.get_json(force=True, silent=True) or {}
        spans = body.get("spans") or []
        if not isinstance(spans, list):
            abort(400)
        device = LOCAL_DEVICE if is_loopback() else f"ext:{str(body.get('device') or 'phone')[:40]}"
        browser = str(body.get("browser") or "browser")[:40]
        rows = []
        now_ts = time.time()
        for s in spans[:500]:
            try:
                url, start, end = str(s["url"])[:2000], float(s["start"]), float(s["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if not url.startswith(("http://", "https://")) or not (0 < start <= end <= now_ts + 60) or end - start > 6 * 3600:
                continue
            rows.append((device, browser, url, str(s.get("title") or "")[:500], start, end))
        if rows:
            zen.db.many("INSERT INTO activity(device, browser, url, title, start, end) VALUES (?,?,?,?,?,?)", rows)
        return jsonify({"accepted": len(rows), "device": device})

    @app.post("/api/token/rotate")
    def rotate():
        if not is_loopback():
            abort(403)
        import secrets
        zen.settings.data["server"]["token"] = secrets.token_urlsafe(18)
        zen.settings.save()
        return jsonify({"token": zen.settings.token})

    @app.post("/api/forget")
    def forget():
        if (request.get_json(force=True, silent=True) or {}).get("confirm") != "forget everything":
            abort(400, 'send {"confirm": "forget everything"}')
        zen.db.wipe()
        shutil.rmtree(zen.imports_dir, ignore_errors=True)
        zen.imports_dir.mkdir(exist_ok=True)
        return jsonify({"forgotten": True})

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def error(exc):
        return jsonify({"error": getattr(exc, "description", str(exc))}), exc.code

    return app


def serve(zen: Zen, host: str = "127.0.0.1", port: int | None = None, watch: bool = True,
          open_browser: bool = False, lan: bool = False) -> None:
    from werkzeug.serving import make_server

    from ..buddy.watcher import Watcher
    port = port or zen.settings["server"].get("port", 7777)
    watcher = Watcher(zen).start() if watch else None
    app = create_app(zen, watcher=watcher, lan=lan)
    server = make_server(host, port, app, threaded=True)
    local_url = f"http://127.0.0.1:{port}/"
    print(f"\n  Zen-chan is watching over you  (｡•̀ᴗ-)✧\n\n  Dashboard:  {local_url}")
    if lan:
        print(f"  Phone/LAN:  http://{lan_ip()}:{port}/?token={zen.settings.token}\n"
              "              (scan the QR code under Sources -> Pair a phone)")
    print("  Stop with Ctrl+C\n")
    if open_browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(local_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if watcher:
            watcher.stop()
