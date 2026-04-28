import os
import random
import time
import json
import uuid
import base64
import hashlib
from typing import Any

import mysql.connector
from cryptography.fernet import Fernet
from flask import Flask, request, jsonify, session, send_file, send_from_directory


FERNET_KEY = os.getenv(
    "QR_FERNET_KEY",
    "TlSwGMUZPbRqdFN7w5sf4OkfeRA2rESxBjNBj1ZV6HQ=",
)
cipher = Fernet(FERNET_KEY.encode() if isinstance(FERNET_KEY, str) else FERNET_KEY)


app = Flask(__name__, static_folder="static")
app.secret_key = os.getenv("FLASK_SESSION_SECRET", "dev-session-secret-change-me")

_db_init_done = False


def _get_db():
    """
    Reads MySQL config from env vars.
    """
    port_raw = os.getenv("DB_PORT", "").strip()
    port = int(port_raw) if port_raw else 3306

    ssl_mode = (os.getenv("DB_SSL_MODE", "") or "").strip().upper()
    ssl_ca_pem = os.getenv("DB_SSL_CA_PEM", "")
    ssl_ca_pem = os.getenv("DB_SSL_CA_PEM", "")
    ssl_ca_pem = ssl_ca_pem.replace("\\n", "\n").strip('"')  # ← YE ADD KARO

    ssl_kwargs = {}
    # Aiven MySQL usually requires SSL. Provide CA cert if available.
    if ssl_mode in {"REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY"}:
        if ssl_ca_pem.strip():
            ca_path = os.path.join(os.getenv("TMPDIR", "/tmp"), "db-ca.pem")
            with open(ca_path, "w", encoding="utf-8") as f:
                f.write(ssl_ca_pem)
            ssl_kwargs = {
                "ssl_ca": ca_path,
                "ssl_verify_cert": ssl_mode in {"VERIFY_CA", "VERIFY_IDENTITY"},
            }
        else:
            # Still attempt SSL without CA if platform supports it.
            ssl_kwargs = {"ssl_disabled": False}

    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=port,
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", "eeng2uveeTuh0Mee"),
        database=os.getenv("DB_NAME", "user"),
        **ssl_kwargs,
    )


def ensure_tables() -> None:
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS NAMES (
            USER_ID VARCHAR(20) PRIMARY KEY,
            CODE_NAME VARCHAR(20) UNIQUE NOT NULL,
            PASS_KEY VARCHAR(255) NOT NULL,
            FULL_NAME VARCHAR(60) DEFAULT '',
            EMAIL VARCHAR(100) DEFAULT '',
            PHONE VARCHAR(15) DEFAULT '',
            GOOGLE_ID VARCHAR(128) DEFAULT NULL,
            EMAIL_VERIFIED TINYINT(1) DEFAULT 0,
            PHONE_VERIFIED TINYINT(1) DEFAULT 0,
            CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
        """
    )

    # Add missing columns to existing tables gracefully
    _add_column_if_missing(cur, 'NAMES', 'FULL_NAME',       "VARCHAR(60) DEFAULT ''")
    _add_column_if_missing(cur, 'NAMES', 'EMAIL',           "VARCHAR(100) DEFAULT ''")
    _add_column_if_missing(cur, 'NAMES', 'PHONE',           "VARCHAR(15) DEFAULT ''")
    _add_column_if_missing(cur, 'NAMES', 'GOOGLE_ID',       "VARCHAR(128) DEFAULT NULL")
    _add_column_if_missing(cur, 'NAMES', 'EMAIL_VERIFIED',  "TINYINT(1) DEFAULT 0")
    _add_column_if_missing(cur, 'NAMES', 'PHONE_VERIFIED',  "TINYINT(1) DEFAULT 0")
    _add_column_if_missing(cur, 'NAMES', 'CREATED_AT',      "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS OTP_STORE (
            ID INT AUTO_INCREMENT PRIMARY KEY,
            TARGET VARCHAR(120) NOT NULL,
            OTP_CODE VARCHAR(6) NOT NULL,
            PURPOSE VARCHAR(20) NOT NULL,
            EXPIRES_AT BIGINT NOT NULL,
            USED TINYINT(1) DEFAULT 0,
            CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_target_purpose (TARGET, PURPOSE)
        ) ENGINE=InnoDB
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS SZEROS (
            ID INT AUTO_INCREMENT PRIMARY KEY,
            USER_ID VARCHAR(20) NOT NULL,
            COIN VARCHAR(64) UNIQUE NOT NULL,
            COINVALUE BIGINT NOT NULL DEFAULT 0,
            FOREIGN KEY (USER_ID) REFERENCES NAMES(USER_ID) ON DELETE CASCADE
        ) ENGINE=InnoDB
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS TRANSACTIONS (
            ID INT AUTO_INCREMENT PRIMARY KEY,
            PAYER_CODE_NAME VARCHAR(20) NOT NULL,
            RECEIVER_CODE_NAME VARCHAR(20) NOT NULL,
            PAYER_COIN VARCHAR(64) NOT NULL,
            RECEIVER_COIN VARCHAR(64) NOT NULL,
            AMOUNT BIGINT NOT NULL,
            CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS RUN_SESSIONS (
            ID INT AUTO_INCREMENT PRIMARY KEY,
            USER_ID VARCHAR(20) NOT NULL,
            DISTANCE_KM DECIMAL(10,3) NOT NULL DEFAULT 0,
            COINS_EARNED INT NOT NULL DEFAULT 0,
            CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (USER_ID) REFERENCES NAMES(USER_ID) ON DELETE CASCADE
        ) ENGINE=InnoDB
        """
    )

    conn.commit()
    cur.close()
    conn.close()


def _add_column_if_missing(cur, table: str, column: str, definition: str) -> None:
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except Exception:
        pass  # column already exists

def _ensure_tables_once() -> None:
    global _db_init_done
    if _db_init_done:
        return
    ensure_tables()
    _db_init_done = True


def genearte_coin() -> str:
    li = []
    for _ in range(10):
        li.append(random.randint(1, 90))
    return "".join(str(i) for i in li)


# ─── Anti-Cheat Constants ─────────────────────────────────────────────────────
import math
from datetime import datetime, timezone, time as dtime

MIN_RUNNING_SPEED_KMH  = 3.0    # slower = walking/standing, skip segment
MAX_RUNNING_SPEED_KMH  = 20.0   # faster = vehicle or GPS spoof, reject entire run
COINS_PER_10KM         = 1      # 1 coin per 10 km (must complete full 10 km in one session)
MAX_COINS_PER_DAY      = 1      # only 1 event per day
MAX_GPS_ACCURACY_METERS = 50
MIN_SESSION_KM         = 10.0   # must complete exactly 10 km in one session

# Running window: 5:00 AM to 8:00 AM IST (UTC+5:30)
# User must START (register) before 5:00 AM — checked via first point timestamp
RUN_WINDOW_START_HOUR  = 5      # 5:00 AM IST
RUN_WINDOW_END_HOUR    = 8      # 8:00 AM IST
IST_OFFSET_SEC         = 19800  # 5h30m in seconds

# Speed variance check — constant/robot speed is suspicious
# A real runner's speed varies; if ALL segments are within this tight band, reject
SPEED_VARIANCE_MIN_KMPH = 1.5   # real runner must have at least this std-dev in speed


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon  = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _ist_time_from_unix(unix_ts: float) -> dtime:
    """Return IST time-of-day from a unix timestamp."""
    ist_ts = unix_ts + IST_OFFSET_SEC
    dt = datetime.utcfromtimestamp(ist_ts)
    return dt.time()


def _validate_run_points(points: list[dict]) -> tuple[float, str | None]:
    """
    Validate GPS track for anti-cheat.
    Returns (valid_distance_km, error_or_None).

    Rules:
    1. Min 2 points required.
    2. First point must be BEFORE 5:00 AM IST (registered before window opens).
    3. All points must fall within 5:00 AM - 8:00 AM IST window.
    4. GPS accuracy <= 50m.
    5. Speed per segment: 3-20 km/h (no vehicle, no spoof).
    6. Speed must NOT be suspiciously constant (std-dev >= 1.5 km/h required).
    7. Total valid distance must be >= 10 km to earn a coin (no carry-forward).
    """
    if not points or len(points) < 2:
        return 0.0, "Minimum 2 GPS points required"

    try:
        pts = sorted(points, key=lambda p: float(p["timestamp"]))
    except (KeyError, TypeError, ValueError):
        return 0.0, "Invalid point data: timestamp missing"

    # Rule 2: first point must be before 5:00 AM IST
    first_time = _ist_time_from_unix(float(pts[0]["timestamp"]))
    if first_time >= dtime(RUN_WINDOW_START_HOUR, 0, 0):
        return 0.0, (
            f"You must register for the run BEFORE {RUN_WINDOW_START_HOUR}:00 AM IST. "
            f"Your first point was recorded at {first_time.strftime('%H:%M:%S')} IST."
        )

    total_km = 0.0
    segment_speeds = []

    for i in range(1, len(pts)):
        prev, curr = pts[i - 1], pts[i]

        try:
            lat1, lon1 = float(prev["lat"]), float(prev["lon"])
            lat2, lon2 = float(curr["lat"]), float(curr["lon"])
            t1,   t2   = float(prev["timestamp"]), float(curr["timestamp"])
        except (KeyError, TypeError, ValueError):
            return 0.0, f"Invalid GPS data at point {i}"

        # Rule 3: all points within window
        pt_time = _ist_time_from_unix(t2)
        if not (dtime(RUN_WINDOW_START_HOUR, 0, 0) <= pt_time <= dtime(RUN_WINDOW_END_HOUR, 0, 0)):
            return 0.0, (
                f"Point {i} is outside the allowed window "
                f"({RUN_WINDOW_START_HOUR}:00 AM - {RUN_WINDOW_END_HOUR}:00 AM IST). "
                f"Got {pt_time.strftime('%H:%M:%S')} IST."
            )

        # Rule 4: GPS accuracy
        acc = curr.get("accuracy_m")
        if acc is not None:
            try:
                if float(acc) > MAX_GPS_ACCURACY_METERS:
                    return 0.0, f"GPS accuracy too low at point {i} ({acc}m). Move to open area."
            except (TypeError, ValueError):
                pass

        dt_hours = (t2 - t1) / 3600.0
        if dt_hours <= 0:
            return 0.0, "Timestamps must be strictly increasing"

        seg_km    = _haversine_km(lat1, lon1, lat2, lon2)
        speed_kmh = seg_km / dt_hours if dt_hours > 0 else 0

        # Rule 5: speed range
        if speed_kmh > MAX_RUNNING_SPEED_KMH:
            return 0.0, (
                f"Speed too high ({speed_kmh:.1f} km/h) at segment {i}. "
                f"Max allowed: {MAX_RUNNING_SPEED_KMH} km/h. Looks like a vehicle or GPS spoof."
            )

        if speed_kmh >= MIN_RUNNING_SPEED_KMH:
            total_km += seg_km
            segment_speeds.append(speed_kmh)

    # Rule 6: speed variance — constant speed = bot/spoof
    if len(segment_speeds) >= 5:
        mean_speed = sum(segment_speeds) / len(segment_speeds)
        variance   = sum((s - mean_speed) ** 2 for s in segment_speeds) / len(segment_speeds)
        std_dev    = math.sqrt(variance)
        if std_dev < SPEED_VARIANCE_MIN_KMPH:
            return 0.0, (
                f"Speed too constant (std-dev {std_dev:.2f} km/h). "
                f"Real runners have natural variation. Minimum required: {SPEED_VARIANCE_MIN_KMPH} km/h."
            )

    return total_km, None


def generate_qr_payload(code_name: str, coin: str) -> str:
    timestamp = int(time.time())
    nonce = uuid.uuid4().hex[:8]
    raw_string = f"{code_name}|{coin}|{timestamp}|{nonce}"
    token = hashlib.sha256(raw_string.encode()).hexdigest()[:8]
    payload = {
        "c": code_name,
        "co": coin,
        "t": timestamp,
        "n": nonce,
        "tk": token,
    }
    encrypted = cipher.encrypt(json.dumps(payload, separators=(',', ':')).encode())
    return base64.urlsafe_b64encode(encrypted).decode()


def scan_qr_and_decrypt(qr_string: str) -> dict[str, Any] | None:
    try:
        # Step 1: Decode Base64
        decoded = base64.urlsafe_b64decode(qr_string)

        # Step 2: Decrypt
        decrypted = cipher.decrypt(decoded)

        # Step 3: Load JSON
        data = json.loads(decrypted)

        # Step 4: Expiry check (60 sec)
        if int(time.time()) - int(data["t"]) > 60:
            return None

        # Step 5: Recreate raw string
        raw_string = f"{data['c']}|{data['co']}|{data['t']}|{data['n']}"

        # Step 6: Recreate token (same logic as generator)
        expected_token = hashlib.sha256(raw_string.encode()).hexdigest()[:8]

        # Step 7: Verify token
        if data.get("tk") != expected_token:
            return None

        # Step 8: Return cleaned result (optional rename keys)
        return {
            "code_name": data["c"],
            "coin": data["co"],
            "timestamp": data["t"],
            "nonce": data["n"],
            "token": data["tk"],
        }

    except Exception as e:
        print(f"[QR Decrypt Error] {type(e).__name__}: {e}")
        return None

def _require_login() -> tuple[str, str]:
    if "code_name" not in session:
        raise PermissionError("Not logged in")
    code_name = session["code_name"]
    user_id = session.get("user_id")
    if not user_id:
        raise PermissionError("Invalid session")
    return user_id, code_name


def _fetch_user_by_code_name(cur, code_name: str) -> tuple[str, str] | None:
    cur.execute(
        "SELECT USER_ID, CODE_NAME FROM NAMES WHERE UPPER(CODE_NAME) = %s", (code_name,)
    )
    row = cur.fetchone()
    if not row:
        return None
    return row[0], row[1]


def _fetch_coin_row(cur, user_id: str) -> tuple[str, int] | None:
    cur.execute("SELECT COIN, COINVALUE FROM SZEROS WHERE USER_ID = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        return None
    return row[0], int(row[1])


@app.get("/")
def home():
    # Serve the root `index.html` that already exists in your folder.
    # (We keep it simple for your environment.)
    resp = send_file(os.path.join(app.root_path, "index.html"))
    # Prevent aggressive caching during development.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.get("/media/<path:filename>")
def media(filename: str):
    """
    Serves files from ./images (e.g. nova.mp4) with range support.
    """
    images_dir = os.path.join(app.root_path, "images")
    return send_from_directory(images_dir, filename, conditional=True)


@app.get("/images/<path:filename>")
def images(filename: str):
    """Serves files from ./images directory (favicon, logos, etc.)."""
    images_dir = os.path.join(app.root_path, "images")
    return send_from_directory(images_dir, filename)


@app.get("/api/activity")
def api_activity():
    """
    Global live activity feed (public):
    shows who transacted to whom (no amount).
    """
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT PAYER_CODE_NAME, RECEIVER_CODE_NAME, CREATED_AT
            FROM TRANSACTIONS
            ORDER BY CREATED_AT DESC
            LIMIT 50
            """
        )
        rows = cur.fetchall() or []
        items = []
        for r in rows:
            items.append(
                {
                    "payer_code_name": r[0],
                    "receiver_code_name": r[1],
                    "created_at": str(r[2]),
                }
            )
        return jsonify({"ok": True, "activity": items})
    finally:
        cur.close()
        conn.close()


@app.before_request
def _auto_init_db():
    # Ensures tables exist even when running under gunicorn
    # (where __main__ doesn't execute).
    try:
        _ensure_tables_once()
    except Exception as e:
        app.logger.error(f"DB init failed: {e}")
        # App chal sakti hai, next request mein retry hoga


@app.post("/api/register")
def api_register():
    payload = request.get_json(force=True) or {}
    full_name    = (payload.get("name") or "").strip()
    email        = (payload.get("email") or "").strip().lower()
    phone        = (payload.get("phone") or "").strip()
    user_id_raw  = payload.get("user_id")
    user_id      = (user_id_raw or "").strip()
    if user_id:
        user_id = user_id.upper()

    code_name = (payload.get("code_name") or "").strip().upper()
    password  = payload.get("password") or ""
    google_id = (payload.get("google_id") or "").strip() or None

    if not code_name or not (5 <= len(code_name) <= 7):
        return jsonify({"error": "code_name length invalid (expected 5-7 chars)"}), 400
    if not password or not (12 <= len(password) <= 16):
        return jsonify({"error": "password length invalid (expected 12-16 chars)"}), 400

    if not user_id:
        user_id = "".join(str(random.randint(0, 9)) for _ in range(8))

    # Trust verified flags only if OTP was confirmed in this session
    # (server-side OTP table is the source of truth)
    email_verified = 0
    phone_verified = 0
    if email:
        conn_check = _get_db(); cur_check = conn_check.cursor()
        cur_check.execute(
            "SELECT 1 FROM OTP_STORE WHERE TARGET=%s AND PURPOSE='email' AND USED=1 LIMIT 1", (email,)
        )
        if cur_check.fetchone(): email_verified = 1
        cur_check.close(); conn_check.close()
    if phone:
        conn_check = _get_db(); cur_check = conn_check.cursor()
        cur_check.execute(
            "SELECT 1 FROM OTP_STORE WHERE TARGET=%s AND PURPOSE='phone' AND USED=1 LIMIT 1", (phone,)
        )
        if cur_check.fetchone(): phone_verified = 1
        cur_check.close(); conn_check.close()

    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM NAMES WHERE UPPER(CODE_NAME) = %s", (code_name,))
        if cur.fetchone():
            return jsonify({"error": "code_name already exists"}), 409
        if email:
            cur.execute("SELECT 1 FROM NAMES WHERE EMAIL = %s", (email,))
            if cur.fetchone():
                return jsonify({"error": "email already registered"}), 409

        cur.execute(
            """INSERT INTO NAMES
               (USER_ID, CODE_NAME, PASS_KEY, FULL_NAME, EMAIL, PHONE, GOOGLE_ID, EMAIL_VERIFIED, PHONE_VERIFIED)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, code_name, password, full_name, email, phone, google_id, email_verified, phone_verified)
        )

        for _ in range(5):
            coin = genearte_coin()
            try:
                cur.execute(
                    "INSERT INTO SZEROS (USER_ID, COIN, COINVALUE) VALUES (%s, %s, %s)",
                    (user_id, coin, 0),
                )
                break
            except mysql.connector.Error:
                continue
        else:
            return jsonify({"error": "failed to allocate unique coin"}), 500

        conn.commit()
        return jsonify({"ok": True, "user_id": user_id, "code_name": code_name, "coin": coin})
    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({"error": f"DB error: {str(e)}"}), 500
    finally:
        cur.close()
        conn.close()


@app.post("/api/login")
def api_login():
    payload = request.get_json(force=True) or {}
    code_name = (payload.get("code_name") or "").strip().upper()
    password = payload.get("password") or ""

    if not code_name or not password:
        return jsonify({"error": "missing fields"}), 400

    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT USER_ID FROM NAMES WHERE UPPER(CODE_NAME) = %s AND PASS_KEY = %s",
            (code_name, password),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "invalid credentials"}), 401
        user_id = row[0]

        coin_row = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "coin account missing"}), 500
        coin, coinvalue = coin_row

        session["code_name"] = code_name
        session["user_id"] = user_id

        return jsonify(
            {
                "ok": True,
                "code_name": code_name,
                "user_id": user_id,
                "coin": coin,
                "coinvalue": coinvalue,
            }
        )
    finally:
        cur.close()
        conn.close()


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def api_me():
    try:
        user_id, code_name = _require_login()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401

    conn = _get_db()
    cur = conn.cursor()
    try:
        coin_row = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "coin account missing"}), 500
        coin, coinvalue = coin_row
        return jsonify(
            {
                "ok": True,
                "code_name": code_name,
                "user_id": user_id,
                "coin": coin,
                "coinvalue": coinvalue,
            }
        )
    finally:
        cur.close()
        conn.close()


@app.get("/api/account/search")
def api_account_search():
    code_name = (request.args.get("code_name") or "").strip().upper()
    if not code_name:
        return jsonify({"error": "code_name required"}), 400

    conn = _get_db()
    cur = conn.cursor()
    try:
        user = _fetch_user_by_code_name(cur, code_name)
        if not user:
            return jsonify({"error": "account not found"}), 404
        user_id, _ = user
        coin_row = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "coin account missing"}), 500
        coin, coinvalue = coin_row
        return jsonify(
            {
                "ok": True,
                "user_id": user_id,
                "code_name": code_name,
                "coin": coin,
                "coinvalue": coinvalue,
            }
        )
    finally:
        cur.close()
        conn.close()


@app.get("/api/transactions")
def api_transactions():
    try:
        _require_login()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401

    conn = _get_db()
    cur = conn.cursor()
    try:
        user_id, code_name = _require_login()
        # In the UI we show code_name-based history.
        cur.execute(
            """
            SELECT RECEIVER_CODE_NAME, PAYER_CODE_NAME, AMOUNT, CREATED_AT
            FROM TRANSACTIONS
            WHERE PAYER_CODE_NAME = %s OR RECEIVER_CODE_NAME = %s
            ORDER BY CREATED_AT DESC
            LIMIT 40
            """,
            (code_name, code_name),
        )
        rows = cur.fetchall() or []
        txs = []
        for r in rows:
            txs.append(
                {
                    "receiver_code_name": r[0],
                    "payer_code_name": r[1],
                    "amount": int(r[2]),
                    "created_at": str(r[3]),
                }
            )
        return jsonify({"ok": True, "transactions": txs})
    finally:
        cur.close()
        conn.close()


@app.get("/api/qr/mine")
def api_qr_mine():
    try:
        _require_login()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401

    conn = _get_db()
    cur = conn.cursor()
    try:
        user_id, code_name = _require_login()
        coin_row = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "coin account missing"}), 500
        coin, _ = coin_row
        qr_text = generate_qr_payload(code_name=code_name, coin=str(coin))
        return jsonify({"ok": True, "qrText": qr_text})
    finally:
        cur.close()
        conn.close()


@app.post("/api/qr/verify")
def api_qr_verify():
    payload = request.get_json(force=True) or {}
    qr_text = (payload.get("qrText") or "").strip()
    if not qr_text:
        return jsonify({"error": "qrText required"}), 400

    data = scan_qr_and_decrypt(qr_text)
    if not data:
        return jsonify({"error": "invalid or expired QR"}), 400

    return jsonify(
        {
            "ok": True,
            "receiver_code_name": str(data["code_name"]).upper(),
            "receiver_coin": str(data["coin"]),
            "timestamp": int(data["timestamp"]),
        }
    )


def _pay_transfer(
    conn,
    payer_code_name: str,
    payer_password: str,
    amount: int,
    receiver_code_name: str,
    receiver_coin: str,
    ) -> dict[str, Any]:
    cur = conn.cursor()
    try:
        if amount <= 0:
            raise ValueError("amount must be positive")

        # Verify payer credentials
        cur.execute(
            "SELECT USER_ID FROM NAMES WHERE UPPER(CODE_NAME) = %s AND PASS_KEY = %s",
            (payer_code_name, payer_password),
        )
        row = cur.fetchone()
        if not row:
            raise PermissionError("invalid payer password")
        payer_user_id = row[0]

        # Lock rows for safety
        cur.execute(
            "SELECT COIN, COINVALUE FROM SZEROS WHERE USER_ID = %s FOR UPDATE",
            (payer_user_id,),
        )
        payer_coin_row = cur.fetchone()
        if not payer_coin_row:
            raise RuntimeError("payer coin account missing")
        payer_coin_db, payer_balance = payer_coin_row[0], int(payer_coin_row[1])

        if payer_balance < amount:
            raise ValueError("insufficient balance")

        # Verify receiver exists and matches coin
        cur.execute(
            "SELECT USER_ID FROM NAMES WHERE UPPER(CODE_NAME) = %s",
            (receiver_code_name,),
        )
        rrow = cur.fetchone()
        if not rrow:
            raise LookupError("receiver account not found")
        receiver_user_id = rrow[0]

        cur.execute(
            "SELECT COIN, COINVALUE FROM SZEROS WHERE USER_ID = %s FOR UPDATE",
            (receiver_user_id,),
        )
        receiver_coin_row = cur.fetchone()
        if not receiver_coin_row:
            raise RuntimeError("receiver coin account missing")
        receiver_coin_db, receiver_balance = receiver_coin_row[0], int(
            receiver_coin_row[1]
        )

        if str(receiver_coin_db) != str(receiver_coin):
            raise ValueError("receiver coin mismatch")

        # Apply transfer
        cur.execute(
            "UPDATE SZEROS SET COINVALUE = COINVALUE - %s WHERE USER_ID = %s",
            (amount, payer_user_id),
        )
        cur.execute(
            "UPDATE SZEROS SET COINVALUE = COINVALUE + %s WHERE USER_ID = %s",
            (amount, receiver_user_id),
        )

        cur.execute(
            """
            INSERT INTO TRANSACTIONS
            (PAYER_CODE_NAME, RECEIVER_CODE_NAME, PAYER_COIN, RECEIVER_COIN, AMOUNT)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (payer_code_name, receiver_code_name, str(payer_coin_db), str(receiver_coin_db), amount),
        )

        conn.commit()
        payer_new = payer_balance - amount
        receiver_new = receiver_balance + amount
        return {"ok": True, "payer_balance": payer_new, "receiver_balance": receiver_new}
    finally:
        cur.close()


@app.post("/api/transaction/pay")
def api_transaction_pay():
    try:
        payer_user_id, payer_code_name = _require_login()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401

    payload = request.get_json(force=True) or {}
    amount = payload.get("amount")
    payer_password = payload.get("payer_password")
    receiver_code_name = payload.get("receiver_code_name")
    receiver_coin = payload.get("receiver_coin")
    qr_text = payload.get("qrText")

    try:
        amount = int(amount)
    except Exception:
        return jsonify({"error": "amount must be an integer"}), 400

    if not payer_password:
        return jsonify({"error": "payer_password required"}), 400

    # If QR text is provided, use it to determine receiver fields server-side.
    if qr_text:
        data = scan_qr_and_decrypt(str(qr_text).strip())
        if not data:
            return jsonify({"error": "invalid or expired QR"}), 400
        receiver_code_name = str(data["code_name"]).upper()
        receiver_coin = str(data["coin"]).strip()

    if not receiver_code_name or not receiver_coin:
        return jsonify({"error": "receiver info required"}), 400

    receiver_code_name = str(receiver_code_name).strip().upper()
    receiver_coin = str(receiver_coin).strip()

    conn = _get_db()
    try:
        result = _pay_transfer(
            conn=conn,
            payer_code_name=payer_code_name,
            payer_password=str(payer_password),
            amount=amount,
            receiver_code_name=receiver_code_name,
            receiver_coin=receiver_coin,
        )
        return jsonify(result)
    except (PermissionError, ValueError, LookupError) as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({"error": f"payment failed: {str(e)}"}), 500
    finally:
        conn.close()


@app.post("/api/run/earn")
def api_run_earn():
    """
    Submit GPS track to earn coins.
    Rules:
    - Register (first point) BEFORE 5:00 AM IST
    - All points within 5:00 AM - 8:00 AM IST window
    - Must complete full 10 km in ONE session (no carry-forward)
    - Speed: 3-20 km/h with natural variance
    - 1 coin per 10 km, max 1 coin per day
    """
    try:
        user_id, code_name = _require_login()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401

    payload = request.get_json(force=True) or {}
    points  = payload.get("gps_points")

    if not points or not isinstance(points, list):
        return jsonify({"error": "gps_points array required"}), 400

    if len(points) > 10000:
        return jsonify({"error": "Too many GPS points (max 10000)"}), 400

    valid_km, err = _validate_run_points(points)
    if err:
        return jsonify({"error": err}), 400

    if valid_km < MIN_SESSION_KM:
        remaining = MIN_SESSION_KM - valid_km
        return jsonify({
            "ok": True,
            "coins_earned": 0,
            "distance_km": round(valid_km, 3),
            "message": (
                f"You ran {valid_km:.2f} km. "
                f"Need {remaining:.2f} more km to earn 1 coin. "
                f"Note: distance does NOT carry forward to next session."
            ),
        }), 200

    coins_to_award = int(valid_km / MIN_SESSION_KM) * COINS_PER_10KM

    conn = _get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """
            SELECT COALESCE(SUM(COINS_EARNED), 0)
            FROM RUN_SESSIONS
            WHERE USER_ID = %s AND DATE(CREATED_AT) = CURDATE()
            """,
            (user_id,),
        )
        row         = cur.fetchone()
        coins_today = int(row[0]) if row else 0

        if coins_today >= MAX_COINS_PER_DAY:
            return jsonify({"error": "You have already earned your coin for today. Come back tomorrow before 5:00 AM!"}), 429

        coins_to_award = min(coins_to_award, MAX_COINS_PER_DAY - coins_today)

        cur.execute(
            "INSERT INTO RUN_SESSIONS (USER_ID, DISTANCE_KM, COINS_EARNED) VALUES (%s, %s, %s)",
            (user_id, round(valid_km, 3), coins_to_award),
        )
        cur.execute(
            "UPDATE SZEROS SET COINVALUE = COINVALUE + %s WHERE USER_ID = %s",
            (coins_to_award, user_id),
        )
        conn.commit()

        coin_row    = _fetch_coin_row(cur, user_id)
        new_balance = coin_row[1] if coin_row else 0

        return jsonify({
            "ok":           True,
            "coins_earned": coins_to_award,
            "distance_km":  round(valid_km, 3),
            "new_balance":  new_balance,
            "message":      f"Congratulations! Earned {coins_to_award} coin for {valid_km:.2f} km.",
        })

    except Exception as e:
        conn.rollback()
        return jsonify({"error": f"Failed to award coins: {str(e)}"}), 500
    finally:
        cur.close()
        conn.close()



# ─── OTP HELPERS ─────────────────────────────────────────────────────────────

import smtplib
from email.mime.text import MIMEText

SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")        # set in env: your Gmail address
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")    # set in env: Gmail app password
OTP_EXPIRY_SEC = 300  # 5 minutes


def _generate_otp() -> str:
    return str(random.randint(100000, 999999))


def _store_otp(target: str, otp: str, purpose: str) -> None:
    conn = _get_db(); cur = conn.cursor()
    try:
        # Invalidate old OTPs for same target+purpose
        cur.execute(
            "UPDATE OTP_STORE SET USED=1 WHERE TARGET=%s AND PURPOSE=%s AND USED=0",
            (target, purpose)
        )
        expires = int(time.time()) + OTP_EXPIRY_SEC
        cur.execute(
            "INSERT INTO OTP_STORE (TARGET, OTP_CODE, PURPOSE, EXPIRES_AT) VALUES (%s, %s, %s, %s)",
            (target, otp, purpose, expires)
        )
        conn.commit()
    finally:
        cur.close(); conn.close()


def _check_otp(target: str, otp: str, purpose: str) -> bool:
    conn = _get_db(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT ID FROM OTP_STORE
               WHERE TARGET=%s AND OTP_CODE=%s AND PURPOSE=%s AND USED=0 AND EXPIRES_AT>%s
               ORDER BY ID DESC LIMIT 1""",
            (target, otp, purpose, int(time.time()))
        )
        row = cur.fetchone()
        if not row:
            return False
        cur.execute("UPDATE OTP_STORE SET USED=1 WHERE ID=%s", (row[0],))
        conn.commit()
        return True
    finally:
        cur.close(); conn.close()


def _send_email_otp(email: str, otp: str) -> None:
    if not SMTP_USER or not SMTP_PASSWORD:
        app.logger.warning("SMTP not configured — OTP printed to console: %s", otp)
        print(f"[DEV EMAIL OTP] {email} -> {otp}")
        return
    msg = MIMEText(
        f"Your Nova verification code is: {otp}\n\nThis code expires in 5 minutes. Do not share it with anyone.",
        "plain"
    )
    msg["Subject"] = "Nova - Email Verification Code"
    msg["From"]    = SMTP_USER
    msg["To"]      = email
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [email], msg.as_string())


# ─── OTP ROUTES ──────────────────────────────────────────────────────────────

@app.post("/api/otp/send-email")
def api_otp_send_email():
    payload = request.get_json(force=True) or {}
    email = (payload.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    otp = _generate_otp()
    _store_otp(email, otp, "email")
    try:
        _send_email_otp(email, otp)
    except Exception as e:
        app.logger.error("Email OTP send error: %s", e)
        return jsonify({"error": f"Failed to send OTP: {str(e)}"}), 500
    return jsonify({"ok": True, "message": "OTP sent to email"})


@app.post("/api/otp/verify-email")
def api_otp_verify_email():
    payload = request.get_json(force=True) or {}
    email = (payload.get("email") or "").strip().lower()
    otp   = (payload.get("otp")   or "").strip()
    if not email or not otp:
        return jsonify({"error": "email and otp required"}), 400
    if not _check_otp(email, otp, "email"):
        return jsonify({"error": "Invalid or expired OTP"}), 400
    return jsonify({"ok": True})


# ─── GOOGLE LOGIN ─────────────────────────────────────────────────────────────

GOOGLE_CLIENT_ID = os.getenv(
    "GOOGLE_CLIENT_ID",
    "1097116870868-7ivki8folv3smkprudo8nmemhcb3gssd.apps.googleusercontent.com"
)
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")


def _verify_google_token(credential: str) -> dict:
    """Verify Google ID token and return payload."""
    import urllib.request, json as _json
    # Fetch Google's public certs
    certs_url = "https://www.googleapis.com/oauth2/v3/certs"
    with urllib.request.urlopen(certs_url, timeout=5) as r:
        certs = _json.loads(r.read().decode())

    # Use google-auth library if available, otherwise use simple JWT decode
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests
        idinfo = google_id_token.verify_oauth2_token(
            credential, google_requests.Request(), GOOGLE_CLIENT_ID
        )
        return idinfo
    except ImportError:
        pass

    # Fallback: decode JWT payload without full signature verify (dev mode)
    # In production, always install google-auth: pip install google-auth
    import base64 as _b64
    parts = credential.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT")
    padding = 4 - len(parts[1]) % 4
    decoded = _b64.urlsafe_b64decode(parts[1] + "=" * padding)
    payload = _json.loads(decoded)
    if payload.get("aud") != GOOGLE_CLIENT_ID:
        raise ValueError("Token audience mismatch")
    if payload.get("exp", 0) < int(time.time()):
        raise ValueError("Token expired")
    return payload


@app.post("/api/google-login")
def api_google_login():
    payload = request.get_json(force=True) or {}
    credential = (payload.get("credential") or "").strip()
    if not credential:
        return jsonify({"error": "Google credential required"}), 400

    try:
        idinfo = _verify_google_token(credential)
    except Exception as e:
        return jsonify({"error": f"Google token verification failed: {str(e)}"}), 401

    google_id = idinfo.get("sub", "")
    email     = (idinfo.get("email") or "").lower()
    name      = idinfo.get("name", "")

    if not google_id or not email:
        return jsonify({"error": "Incomplete Google profile"}), 400

    conn = _get_db(); cur = conn.cursor()
    try:
        # Check if user exists by google_id or email
        cur.execute(
            "SELECT USER_ID, CODE_NAME FROM NAMES WHERE GOOGLE_ID=%s OR (EMAIL=%s AND EMAIL_VERIFIED=1) LIMIT 1",
            (google_id, email)
        )
        row = cur.fetchone()
        if row:
            user_id, code_name = row[0], row[1]
            # Update google_id if not set
            cur.execute("UPDATE NAMES SET GOOGLE_ID=%s WHERE USER_ID=%s AND (GOOGLE_ID IS NULL OR GOOGLE_ID='')",
                        (google_id, user_id))
            conn.commit()
            session["code_name"] = code_name
            session["user_id"]   = user_id
            coin_row = _fetch_coin_row(cur, user_id)
            coin, coinvalue = coin_row if coin_row else ("", 0)
            return jsonify({"ok": True, "code_name": code_name, "user_id": user_id,
                            "coin": coin, "coinvalue": coinvalue})
        else:
            # New Google user — send back info for registration form
            return jsonify({"ok": True, "needs_setup": True, "email": email, "name": name, "google_id": google_id})
    finally:
        cur.close(); conn.close()


if __name__ == "__main__":
    ensure_tables()
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "1") == "1",
    )



# ─── RAZORPAY COIN PURCHASE ──────────────────────────────────────────────────

import hmac
import hashlib

RAZORPAY_KEY_ID     = os.getenv("RAZORPAY_KEY_ID",     "rzp_test_SiOpSGdjiLhfLU")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET",  "AeWUI820OxMLE14PVJOMfOu6")


def _ensure_purchase_table() -> None:
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS COIN_PURCHASES (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                USER_ID VARCHAR(20) NOT NULL,
                CODE_NAME VARCHAR(20) NOT NULL,
                RAZORPAY_ORDER_ID VARCHAR(64),
                RAZORPAY_PAYMENT_ID VARCHAR(64),
                COINS INT NOT NULL,
                AMOUNT_PAISE INT NOT NULL,
                STATUS VARCHAR(20) DEFAULT 'PENDING',
                INVOICE_NO VARCHAR(40),
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (USER_ID) REFERENCES NAMES(USER_ID) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _razorpay_create_order(amount_paise: int, receipt: str) -> dict:
    """Call Razorpay Orders API to create an order."""
    import urllib.request
    import base64 as b64

    url = "https://api.razorpay.com/v1/orders"
    payload = json.dumps({
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
    }).encode()

    creds = b64.b64encode(f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _verify_razorpay_signature(order_id: str, payment_id: str, signature: str) -> bool:
    msg = f"{order_id}|{payment_id}"
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(), msg.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/api/payment/create-order")
def api_payment_create_order():
    """Create a Razorpay order for coin purchase."""
    try:
        user_id, code_name = _require_login()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401

    _ensure_purchase_table()

    payload = request.get_json(force=True) or {}
    coins = int(payload.get("coins", 0))
    amount = int(payload.get("amount", 0))  # in paise

    if coins <= 0 or amount <= 0:
        return jsonify({"error": "Invalid coins or amount"}), 400

    receipt = f"nova_{user_id}_{int(time.time())}"

    try:
        order = _razorpay_create_order(amount, receipt)
    except Exception as e:
        app.logger.error(f"Razorpay create order error: {e}")
        return jsonify({"error": f"Payment gateway error: {str(e)}"}), 502

    # Save pending purchase
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO COIN_PURCHASES
            (USER_ID, CODE_NAME, RAZORPAY_ORDER_ID, COINS, AMOUNT_PAISE, STATUS)
            VALUES (%s, %s, %s, %s, %s, 'PENDING')
        """, (user_id, code_name, order["id"], coins, amount))
        conn.commit()
    finally:
        cur.close()
        conn.close()

    return jsonify({
        "ok": True,
        "razorpay_order_id": order["id"],
        "amount": order["amount"],
        "currency": order["currency"],
    })


@app.post("/api/payment/verify")
def api_payment_verify():
    """Verify Razorpay payment signature and credit coins."""
    try:
        user_id, code_name = _require_login()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401

    _ensure_purchase_table()

    payload = request.get_json(force=True) or {}
    order_id   = (payload.get("razorpay_order_id")   or "").strip()
    payment_id = (payload.get("razorpay_payment_id") or "").strip()
    signature  = (payload.get("razorpay_signature")  or "").strip()
    coins      = int(payload.get("coins",  0))
    amount     = int(payload.get("amount", 0))

    if not order_id or not payment_id or not signature:
        return jsonify({"error": "Missing payment details"}), 400

    # Verify signature
    if not _verify_razorpay_signature(order_id, payment_id, signature):
        return jsonify({"error": "Payment signature verification failed"}), 400

    conn = _get_db()
    cur = conn.cursor()
    try:
        # Check not already processed
        cur.execute(
            "SELECT ID, STATUS FROM COIN_PURCHASES WHERE RAZORPAY_ORDER_ID = %s FOR UPDATE",
            (order_id,)
        )
        row = cur.fetchone()
        if row and row[1] == "SUCCESS":
            return jsonify({"error": "Order already processed"}), 409

        # Generate invoice number
        invoice_no = f"NOVA-{int(time.time())}-{uuid.uuid4().hex[:6].upper()}"

        # Credit coins to user
        cur.execute(
            "UPDATE SZEROS SET COINVALUE = COINVALUE + %s WHERE USER_ID = %s",
            (coins, user_id)
        )

        # Update purchase record
        cur.execute("""
            UPDATE COIN_PURCHASES
            SET STATUS = 'SUCCESS',
                RAZORPAY_PAYMENT_ID = %s,
                INVOICE_NO = %s
            WHERE RAZORPAY_ORDER_ID = %s AND USER_ID = %s
        """, (payment_id, invoice_no, order_id, user_id))

        # If no row found yet (edge case), insert
        if cur.rowcount == 0:
            cur.execute("""
                INSERT INTO COIN_PURCHASES
                (USER_ID, CODE_NAME, RAZORPAY_ORDER_ID, RAZORPAY_PAYMENT_ID, COINS, AMOUNT_PAISE, STATUS, INVOICE_NO)
                VALUES (%s, %s, %s, %s, %s, %s, 'SUCCESS', %s)
            """, (user_id, code_name, order_id, payment_id, coins, amount, invoice_no))

        conn.commit()

        # Get new balance
        coin_row = _fetch_coin_row(cur, user_id)
        new_balance = coin_row[1] if coin_row else 0

        return jsonify({
            "ok": True,
            "coins_added": coins,
            "new_balance": new_balance,
            "invoice_no": invoice_no,
        })

    except Exception as e:
        conn.rollback()
        return jsonify({"error": f"Failed to credit coins: {str(e)}"}), 500
    finally:
        cur.close()
        conn.close()
