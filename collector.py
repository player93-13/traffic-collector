#!/usr/bin/env python3

import os
import time
import json
import subprocess
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import execute_values
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------- Logging ----------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------- Config ----------------
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS")
}

INTERVAL = int(os.getenv("INTERVAL", "60"))
XRAY_API = os.getenv("XRAY_API")
XRAY_BIN = os.getenv("XRAY_BIN", "xray")
WG_CONTAINER = os.getenv("WG_CONTAINER")
WG_INTERFACE = os.getenv("WG_INTERFACE")

HEALTH_BIND = os.getenv("HEALTH_BIND", "127.0.0.1")
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "9229"))

running = True
db_pool = None

# ---------------- Signals ----------------
def handle_signal(signum, frame):
    global running
    logger.info("Received signal %s, shutting down...", signum)
    running = False

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# ---------------- Health ----------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

def start_health_server():
    try:
        server = HTTPServer((HEALTH_BIND, HEALTH_PORT), HealthHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        logger.info("Health server on %s:%d", HEALTH_BIND, HEALTH_PORT)
        return server
    except Exception as e:
        logger.warning("Health server failed: %s", e)
        return None

# ---------------- DB Pool ----------------
def init_db_pool():
    global db_pool
    db_pool = SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        **DB_CONFIG
    )
    logger.info("DB pool initialized")

def get_conn():
    return db_pool.getconn()

def put_conn(conn):
    db_pool.putconn(conn)

def init_db(conn):
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            source TEXT,
            external_id TEXT,
            name TEXT,
            UNIQUE(source, external_id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            id BIGSERIAL PRIMARY KEY,
            ts INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            rx BIGINT NOT NULL,
            tx BIGINT NOT NULL
        )
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_stats_user_ts
        ON stats(user_id, ts)
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS last_stats (
            user_id INTEGER PRIMARY KEY,
            rx BIGINT,
            tx BIGINT
        )
        """)

    conn.commit()

# ---------------- Helpers ----------------
def get_last(cur, user_id):
    cur.execute("SELECT rx, tx FROM last_stats WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    return row if row else (0, 0)

def update_last(cur, user_id, rx, tx):
    cur.execute("""
    INSERT INTO last_stats (user_id, rx, tx)
    VALUES (%s, %s, %s)
    ON CONFLICT (user_id)
    DO UPDATE SET rx=EXCLUDED.rx, tx=EXCLUDED.tx
    """, (user_id, rx, tx))

def build_cache(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, source, external_id FROM users")
        return {(src, ext): uid for uid, src, ext in cur.fetchall()}

# ---------------- Commands ----------------
def run_cmd(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return None
        return out.stdout
    except Exception:
        return None

# ---------------- XRAY ----------------
def get_xray_stats():
    out = run_cmd([XRAY_BIN, "api", "statsquery", f"--server={XRAY_API}"])
    if not out:
        return {}
    try:
        data = json.loads(out)
    except:
        return {}

    users = {}
    for item in data.get("stat", []):
        name = item.get("name", "")
        val = item.get("value", 0)
        if name.startswith("user>>>"):
            parts = name.split(">>>")
            if len(parts) >= 4:
                _, user, _, direction = parts[:4]
                users.setdefault(user, {"uplink": 0, "downlink": 0})
                users[user][direction] = val
    return users

def collect_xray(conn, cache):
    data = get_xray_stats()
    ts = int(time.time())

    with conn.cursor() as cur:
        rows = []

        for user, stats in data.items():
            key = ("xray", user)
            if key not in cache:
                continue

            uid = cache[key]
            rx = stats.get("downlink", 0)
            tx = stats.get("uplink", 0)

            last_rx, last_tx = get_last(cur, uid)
            d_rx = max(0, rx - last_rx)
            d_tx = max(0, tx - last_tx)

            if d_rx or d_tx:
                rows.append((ts, uid, d_rx, d_tx))

            update_last(cur, uid, rx, tx)

        if rows:
            execute_values(cur,
                "INSERT INTO stats (ts, user_id, rx, tx) VALUES %s",
                rows
            )

    conn.commit()
    logger.info("[XRAY] %d users", len(data))

# ---------------- WG ----------------
def collect_wg(conn, cache):
    out = run_cmd(["docker", "exec", WG_CONTAINER, "wg", "show", WG_INTERFACE])
    if not out:
        return

    ts = int(time.time())

    with conn.cursor() as cur:
        rows = []
        peer = None

        for line in out.splitlines():
            line = line.strip()

            if line.startswith("peer:"):
                peer = line.split()[1]

            elif "transfer:" in line and peer:
                try:
                    parts = line.split("transfer:")[1].split(",")
                    rx = int(parts[0].strip().split()[0])
                    tx = int(parts[1].strip().split()[0])
                except:
                    continue

                key = ("wg", peer)
                if key not in cache:
                    continue

                uid = cache[key]
                last_rx, last_tx = get_last(cur, uid)

                d_rx = max(0, rx - last_rx)
                d_tx = max(0, tx - last_tx)

                if d_rx or d_tx:
                    rows.append((ts, uid, d_rx, d_tx))

                update_last(cur, uid, rx, tx)

        if rows:
            execute_values(cur,
                "INSERT INTO stats (ts, user_id, rx, tx) VALUES %s",
                rows
            )

    conn.commit()
    logger.info("[WG] collected")

# ---------------- MAIN ----------------
def main():
    init_db_pool()
    server = start_health_server()

    while running:
        conn = None
        try:
            conn = get_conn()
            init_db(conn)

            cache = build_cache(conn)

            if XRAY_API:
                collect_xray(conn, cache)

            if WG_CONTAINER and WG_INTERFACE:
                collect_wg(conn, cache)

        except Exception as e:
            logger.exception("Main loop error: %s", e)
            time.sleep(2)

        finally:
            if conn:
                put_conn(conn)

        for _ in range(INTERVAL):
            if not running:
                break
            time.sleep(1)

    if server:
        server.shutdown()

    db_pool.closeall()
    logger.info("Shutdown complete")

if __name__ == "__main__":
    main()