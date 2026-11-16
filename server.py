#!/usr/bin/env python3

import argparse
import json
import platform
import re
import socket
import sqlite3
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "ComplianceAutoFilter.html"


# ----------------------------------------------------------------------
# Database — SQLite, three tables holding the same three things the app
# reads/writes as one JSON blob over HTTP (the API shape below is UNCHANGED
# from the old JSON-file version — this is purely a storage-engine swap, the
# browser side needs no changes):
#   current         — the ONE live report shown on Upload & Run / Results.
#                      Running a new CSV completely replaces it (single row).
#   reports         — the PERMANENT dated archive (Daily Summary / Monthly
#                      Archive / Monthly Report), one row per date.
#   monthly_report  — Monthly Report's manual SAT-A / Total Device / Target
#                      inputs, one row per "YYYY-MM".
#
# WAL (Write-Ahead Logging) mode is the whole reason for the "without
# interruption" behavior: in WAL mode, readers never block on a writer and a
# writer never blocks on readers — a GET from another PC's 30-second poll is
# served instantly from the last-committed snapshot even while someone else
# is mid-POST. Only two writers can ever collide, and busy_timeout below
# just makes the second one wait briefly and retry instead of failing.
#
# Every request opens its own short-lived connection (see Handler.get_conn)
# rather than sharing one — connections are cheap in SQLite, and this is the
# standard-recommended pattern for a multi-threaded server, since it avoids
# any risk of one request's transaction state leaking into another's.
# ----------------------------------------------------------------------
def get_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")   # safe pairing with WAL, much faster than FULL
    conn.execute("PRAGMA busy_timeout=5000;")    # wait up to 5s on a write collision instead of erroring
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS current (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT);
        CREATE TABLE IF NOT EXISTS reports (date TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS monthly_report (month TEXT PRIMARY KEY, data TEXT NOT NULL);
        """
    )
    conn.commit()


def migrate_legacy_json_if_present(conn: sqlite3.Connection, db_path: Path):
    """One-time upgrade path for anyone coming from the old JSON-file
    version: if this SQLite database is brand new (no saved reports yet) and
    a legacy compliance_data.json sits next to it, import everything from
    that file so nobody loses history switching over. The old file is then
    renamed to *.migrated.json (never deleted) so this only ever runs once
    and the original is kept as a plain-text backup."""
    existing = conn.execute("SELECT COUNT(*) AS n FROM reports").fetchone()["n"]
    if existing > 0:
        return  # already has real data — never auto-migrate on top of it

    legacy_path = db_path.parent / "compliance_data.json"
    if not legacy_path.exists():
        return

    try:
        with open(legacy_path, "r", encoding="utf-8") as f:
            legacy = json.load(f)
    except Exception as e:
        print(f"WARNING: found {legacy_path.name} but couldn't read it ({e}) — skipping migration.")
        return
    if not isinstance(legacy, dict):
        print(f"WARNING: {legacy_path.name} doesn't look like a valid database — skipping migration.")
        return

    n_reports = 0
    with conn:
        current = legacy.get("current")
        if current is not None:
            conn.execute("INSERT OR REPLACE INTO current (id, data) VALUES (1, ?)", (json.dumps(current),))
        for date, entry in (legacy.get("reports") or {}).items():
            conn.execute("INSERT OR REPLACE INTO reports (date, data) VALUES (?, ?)", (date, json.dumps(entry)))
            n_reports += 1
        for month, entry in (legacy.get("monthlyReport") or {}).items():
            conn.execute("INSERT OR REPLACE INTO monthly_report (month, data) VALUES (?, ?)", (month, json.dumps(entry)))

    migrated_path = legacy_path.with_name(legacy_path.stem + ".migrated.json")
    try:
        legacy_path.rename(migrated_path)
    except Exception:
        migrated_path = legacy_path  # rename failed (e.g. permissions) — leave it in place, still fine

    print(f"Migrated {n_reports} saved day(s) from {legacy_path.name} into the new SQLite database.")
    print(f"(Kept the old file as {migrated_path.name} — safe to delete once you've confirmed everything looks right.)")


def load_data(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT data FROM current WHERE id = 1").fetchone()
    current = json.loads(row["data"]) if row and row["data"] else None

    reports = {r["date"]: json.loads(r["data"]) for r in conn.execute("SELECT date, data FROM reports")}
    monthly_report = {r["month"]: json.loads(r["data"]) for r in conn.execute("SELECT month, data FROM monthly_report")}

    return {"current": current, "reports": reports, "monthlyReport": monthly_report}


def _replace_table(conn: sqlite3.Connection, table: str, key_col: str, incoming: dict):
    """Makes `table` match `incoming` exactly — upserts every incoming row,
    deletes any row not present anymore. The browser always sends its whole
    intended final state (it fetches the full blob, edits in memory, posts
    the whole thing back), so "make the table equal this dict" is the
    correct operation, not "add these rows" — same replace-all semantics the
    old single JSON file always had. Diffing against what's already there
    (instead of blindly DELETE-ALL + INSERT-ALL every time) keeps each write
    fast even once there are years of saved days, which matters here since
    it's what keeps the write transaction — and therefore any brief overlap
    with concurrent readers — as short as possible."""
    existing_keys = {row[0] for row in conn.execute(f"SELECT {key_col} FROM {table}")}
    incoming_keys = set(incoming.keys())
    for k in existing_keys - incoming_keys:
        conn.execute(f"DELETE FROM {table} WHERE {key_col} = ?", (k,))
    for k, v in incoming.items():
        conn.execute(
            f"INSERT INTO {table} ({key_col}, data) VALUES (?, ?) "
            f"ON CONFLICT({key_col}) DO UPDATE SET data = excluded.data",
            (k, json.dumps(v)),
        )


def save_data(conn: sqlite3.Connection, data: dict):
    if not isinstance(data, dict):
        data = {}
    current = data.get("current")
    reports = data.get("reports") or {}
    monthly_report = data.get("monthlyReport") or {}

    with conn:  # one transaction — all-or-nothing, same guarantee the old atomic file write gave
        if current is None:
            conn.execute("DELETE FROM current WHERE id = 1")
        else:
            conn.execute("INSERT OR REPLACE INTO current (id, data) VALUES (1, ?)", (json.dumps(current),))
        _replace_table(conn, "reports", "date", reports)
        _replace_table(conn, "monthly_report", "month", monthly_report)


# ----------------------------------------------------------------------
# Ping — runs a real ICMP ping from the server (subprocess to the OS ping
# command), since browsers cannot send raw ICMP themselves.
# ----------------------------------------------------------------------
def is_valid_ip(ip):
    if not bool(re.match(r"^(?:\d{1,3}\.){3}\d{1,3}$", ip or "")):
        return False
    try:
        return all(0 <= int(p) <= 255 for p in ip.split("."))
    except ValueError:
        return False


def ping_host(ip):
    if not is_valid_ip(ip):
        return False
    system = platform.system().lower()
    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", "1000", ip]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return result.returncode == 0
    except Exception:
        return False


# ----------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "ComplianceAutoFilter/3.0"
    db_path = None  # injected in main()
    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.address_string(), fmt % args))

    # ---- helpers ----
    def get_conn(self) -> sqlite3.Connection:
        return get_conn(self.db_path)

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    # ---- CORS preflight ----
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    # ---- routing ----
    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html", "/ComplianceAutoFilter.html"):
            if not HTML_FILE.exists():
                self.send_json(500, {"error": "ComplianceAutoFilter.html not found next to server.py"})
                return
            self.send_html(HTML_FILE.read_text(encoding="utf-8"))
            return

        if path == "/api/health":
            conn = self.get_conn()
            try:
                n = conn.execute("SELECT COUNT(*) AS n FROM reports").fetchone()["n"]
            finally:
                conn.close()
            self.send_json(200, {"status": "ok", "dbPath": str(self.db_path), "savedReports": n})
            return

        if path == "/api/data":
            conn = self.get_conn()
            try:
                data = load_data(conn)
            finally:
                conn.close()
            self.send_json(200, data)
            return

        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/data":
            try:
                data = self.read_json_body()
            except Exception as e:
                self.send_json(400, {"error": str(e)})
                return
            if not isinstance(data, dict):
                self.send_json(400, {"error": "Expected a JSON object"})
                return
            conn = self.get_conn()
            try:
                save_data(conn, data)
            except Exception as e:
                self.send_json(500, {"error": f"Database write failed: {e}"})
                return
            finally:
                conn.close()
            self.send_json(200, {"ok": True})
            return

        if path == "/api/ping":
            try:
                req = self.read_json_body()
                ip = str(req.get("ip", "")).strip()
                if not is_valid_ip(ip):
                    self.send_json(400, {"error": "Invalid IP address"})
                    return
                online = ping_host(ip)
                self.send_json(200, {"ok": True, "online": online, "ip": ip})
            except Exception as e:
                self.send_json(400, {"error": str(e)})
            return

        self.send_json(404, {"error": "Not found"})


# A larger listen backlog than the http.server default (5) — several PCs
# reconnecting at once (e.g. right after a network blip, or many browser
# tabs open) can otherwise have a connection or two rejected at the TCP
# level before it ever reaches our code. Cheap to raise, no downside.
class Server(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True
    allow_reuse_address = True


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_all_lan_ips():
    """Return every non-loopback IPv4 address on this PC. Useful when a VPN or
    virtual adapter (Hyper-V, Docker, corporate VPN, etc.) causes get_lan_ip()
    to pick an interface that isn't reachable from the office LAN."""
    ips = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def main():
    parser = argparse.ArgumentParser(description="Compliance Auto-Filter centralized server")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default 8000)")
    parser.add_argument(
        "--db",
        type=str,
        default=str(BASE_DIR / "compliance_data.db"),
        help="Path to the SQLite database file (default: compliance_data.db next to this script)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Bind address (default 0.0.0.0 — reachable from other PCs on the network)",
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    startup_conn = get_conn(db_path)
    init_schema(startup_conn)
    migrate_legacy_json_if_present(startup_conn, db_path)
    saved_reports = startup_conn.execute("SELECT COUNT(*) AS n FROM reports").fetchone()["n"]
    startup_conn.close()

    if not HTML_FILE.exists():
        print(f"WARNING: {HTML_FILE.name} not found in {BASE_DIR} — the page won't load until it's placed here.")

    Handler.db_path = db_path
    httpd = Server((args.host, args.port), Handler)

    lan_ip = get_lan_ip()
    all_ips = get_all_lan_ips()
    print("=" * 64)
    print(" Compliance Auto-Filter — Centralized Server")
    print("=" * 64)
    print(f" Database file : {db_path}  (SQLite, WAL mode)")
    print(f" Saved reports : {saved_reports}")
    print()
    print(f" On this PC      ->  http://localhost:{args.port}")
    print(f" From other PCs  ->  http://{lan_ip}:{args.port}")
    if len(all_ips) > 1:
        others = [ip for ip in all_ips if ip != lan_ip]
        print()
        print(" This PC has more than one network address. If teammates can't")
        print(" connect using the address above, try one of these instead")
        print(" (a VPN or virtual adapter may be interfering):")
        for ip in others:
            print(f"   -> http://{ip}:{args.port}")
    print()
    print(" Everyone who opens that link reads/writes the SAME database.")
    print(" Data auto-syncs every 30 seconds, plus instantly after any save.")
    print(" WAL mode means that 30-second refresh on every other PC is never")
    print(" blocked or interrupted by someone else saving data at the same time.")
    print(" If other PCs can't connect: check Windows Firewall allows this")
    print(" app on Private networks, and confirm everyone is on the same")
    print(" network/VPN as this PC.")
    print(" Press Ctrl+C to stop the server.")
    print("=" * 64)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        httpd.shutdown()


if __name__ == "__main__":
    main()

