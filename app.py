"""
Nova – Flask backend
====================
Refactored with OOP, full local + Render (gunicorn) compatibility,
and all original bugs fixed.

Bugs fixed
----------
1. hmac.new() missing digestmod — fixed to explicit hashlib.sha256.
2. RAZORPAY_* constants placed AFTER `if __name__ == "__main__"` — moved to Config.
3. Duplicate ssl_ca_pem = os.getenv(...) line removed.
4. _require_login() called twice inside api_transactions — fixed to one call.
5. genearte_coin typo — renamed to _generate_coin().
6. _ensure_tables_once used bare global bool (not thread-safe) — replaced with threading.Lock.
7. _store_init_done declared but never guarded anything — removed.
8. if __name__ == "__main__": ensure_tables() placed MID-FILE — moved to bottom.
9. Missing python-dotenv auto-load for local dev — added with graceful fallback.
10. _send_email_otp quit() only called in finally (was missing on happy path) — fixed.
11. STORE_ADMIN_CODE_NAME now read from env via Config.
"""

# ── Standard library ──────────────────────────────────────────────────────────
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import random
import re as _re
import smtplib
import threading
import time
import uuid
from datetime import datetime, time as dtime
from email.message import EmailMessage
from typing import Any, Optional
import tempfile
import random
import time
import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

# ── Third-party ───────────────────────────────────────────────────────────────
import mysql.connector
from cryptography.fernet import Fernet
from flask import Flask, jsonify, request, send_file, send_from_directory, session

# ── Load .env for local development ──────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # On Render, env vars are set natively; dotenv is optional locally


# =============================================================================
# CONFIGURATION
# =============================================================================

class Config:
    """Central configuration loaded from environment variables."""

    # Flask
    SESSION_SECRET: str = os.getenv("FLASK_SESSION_SECRET", "").strip()
    DEBUG: bool         = os.getenv("FLASK_DEBUG", "0") == "1"
    PORT: int           = int(os.getenv("PORT", "5000"))

    # Database
    DB_HOST:     str = os.getenv("DB_HOST",     "localhost")
    DB_PORT:     int = int(os.getenv("DB_PORT",  "3306") or "3306")
    DB_USER:     str = os.getenv("DB_USER",     "root")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_NAME:     str = os.getenv("DB_NAME",     "nova")
    DB_SSL_MODE: str = (os.getenv("DB_SSL_MODE", "") or "").strip().upper()
    DB_SSL_CA_PEM: str = (
        os.getenv("DB_SSL_CA_PEM", "")
        .replace("\\n", "\n")
        .strip('"')
        .strip()
    )

    # QR / Fernet
    FERNET_KEY: str = os.getenv("QR_FERNET_KEY", "").strip()

    # Google OAuth
    GOOGLE_CLIENT_ID:     str = os.getenv("GOOGLE_CLIENT_ID",     "").strip()
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

    # Razorpay  (BUG FIX: were defined after __main__ block — now at module top)
    RAZORPAY_KEY_ID:     str = os.getenv("RAZORPAY_KEY_ID",     "").strip()
    RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "").strip()

    # SMTP
    SMTP_HOST:     str = os.getenv("SMTP_HOST",     "smtp.gmail.com")
    SMTP_PORT:     int = int(os.getenv("SMTP_PORT",  "587") or "587")
    SMTP_USER:     str = os.getenv("SMTP_USER",     "").strip()
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "").strip()

    # Store admin
    STORE_ADMIN_CODE_NAME: str = os.getenv("STORE_ADMIN_CODE_NAME", "SHADOW").strip().upper()

    @classmethod
    def validate(cls) -> None:
        """Raise RuntimeError for missing mandatory secrets."""
        if not cls.FERNET_KEY:
            raise RuntimeError(
                "QR_FERNET_KEY is not set. "
                "Generate: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        if not cls.SESSION_SECRET:
            raise RuntimeError(
                "FLASK_SESSION_SECRET is not set. "
                "Generate: python -c \"import secrets; print(secrets.token_hex(32))\""
            )


Config.validate()


# =============================================================================
# ANTI-CHEAT CONSTANTS
# =============================================================================

MIN_RUNNING_SPEED_KMH    = 3.0
MAX_RUNNING_SPEED_KMH    = 20.0
COINS_PER_10KM           = 1
MAX_COINS_PER_DAY        = 1
MAX_GPS_ACCURACY_METERS  = 50
MIN_SESSION_KM           = 10.0
RUN_WINDOW_START_HOUR    = 5
RUN_WINDOW_END_HOUR      = 8
IST_OFFSET_SEC           = 19800   # UTC+5:30
SPEED_VARIANCE_MIN_KMPH  = 1.5
OTP_EXPIRY_SEC           = 300


# =============================================================================
# DATABASE MANAGER
# =============================================================================

class DatabaseManager:
    """Handles all DB connection and thread-safe schema initialisation."""

    _init_lock: threading.Lock = threading.Lock()
    _init_done: bool           = False

    @staticmethod
    def get_connection() -> mysql.connector.MySQLConnection:
        ssl_kwargs: dict = {}
        if Config.DB_SSL_MODE in {"REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY"}:
            if Config.DB_SSL_CA_PEM:
                # ca_path = os.path.join(os.getenv("TMPDIR", "/tmp"), "db-ca.pem")
                ca_path = os.path.join(tempfile.gettempdir(), "db-ca.pem")
                with open(ca_path, "w", encoding="utf-8") as f:
                    f.write(Config.DB_SSL_CA_PEM)
                ssl_kwargs = {
                    "ssl_ca": ca_path,
                    "ssl_verify_cert": Config.DB_SSL_MODE in {"VERIFY_CA", "VERIFY_IDENTITY"},
                }
            else:
                ssl_kwargs = {"ssl_disabled": False}

        return mysql.connector.connect(
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            database=Config.DB_NAME,
            **ssl_kwargs,
        )

    @classmethod
    def ensure_tables_once(cls) -> None:
        """Thread-safe one-time schema initialisation (BUG FIX: was not thread-safe)."""
        if cls._init_done:
            return
        with cls._init_lock:
            if cls._init_done:
                return
            cls._create_tables()
            cls._init_done = True

    @classmethod
    def _create_tables(cls) -> None:
        conn = cls.get_connection()
        cur  = conn.cursor()
        try:
            cur.execute("""
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
            """)
            for col, defn in [
                ("FULL_NAME",      "VARCHAR(60) DEFAULT ''"),
                ("EMAIL",          "VARCHAR(100) DEFAULT ''"),
                ("PHONE",          "VARCHAR(15) DEFAULT ''"),
                ("GOOGLE_ID",      "VARCHAR(128) DEFAULT NULL"),
                ("EMAIL_VERIFIED", "TINYINT(1) DEFAULT 0"),
                ("PHONE_VERIFIED", "TINYINT(1) DEFAULT 0"),
                ("CREATED_AT",     "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ]:
                cls._add_column_if_missing(cur, "NAMES", col, defn)

            cur.execute("""
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
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS SZEROS (
                    ID INT AUTO_INCREMENT PRIMARY KEY,
                    USER_ID VARCHAR(20) NOT NULL,
                    COIN VARCHAR(64) UNIQUE NOT NULL,
                    COINVALUE BIGINT NOT NULL DEFAULT 0,
                    FOREIGN KEY (USER_ID) REFERENCES NAMES(USER_ID) ON DELETE CASCADE
                ) ENGINE=InnoDB
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS TRANSACTIONS (
                    ID INT AUTO_INCREMENT PRIMARY KEY,
                    PAYER_CODE_NAME VARCHAR(20) NOT NULL,
                    RECEIVER_CODE_NAME VARCHAR(20) NOT NULL,
                    PAYER_COIN VARCHAR(64) NOT NULL,
                    RECEIVER_COIN VARCHAR(64) NOT NULL,
                    AMOUNT BIGINT NOT NULL,
                    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS RUN_SESSIONS (
                    ID INT AUTO_INCREMENT PRIMARY KEY,
                    USER_ID VARCHAR(20) NOT NULL,
                    DISTANCE_KM DECIMAL(10,3) NOT NULL DEFAULT 0,
                    COINS_EARNED INT NOT NULL DEFAULT 0,
                    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (USER_ID) REFERENCES NAMES(USER_ID) ON DELETE CASCADE
                ) ENGINE=InnoDB
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS STORE_PRODUCTS (
                    ID INT AUTO_INCREMENT PRIMARY KEY,
                    NAME VARCHAR(120) NOT NULL,
                    SLUG VARCHAR(120) UNIQUE NOT NULL,
                    PRICE_COINS DECIMAL(10,4) NOT NULL DEFAULT 1.0,
                    PRICE_INR INT NOT NULL DEFAULT 10000,
                    DESCRIPTION TEXT,
                    FEATURES TEXT,
                    IMAGE_URL VARCHAR(500) DEFAULT '',
                    CATEGORY VARCHAR(60) DEFAULT 'watches',
                    STOCK INT NOT NULL DEFAULT 10,
                    ACTIVE TINYINT(1) DEFAULT 1,
                    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS STORE_ORDERS (
                    ID INT AUTO_INCREMENT PRIMARY KEY,
                    ORDER_NO VARCHAR(40) UNIQUE NOT NULL,
                    USER_ID VARCHAR(20) NOT NULL,
                    CODE_NAME VARCHAR(20) NOT NULL,
                    PRODUCT_ID INT NOT NULL,
                    PRODUCT_NAME VARCHAR(120) NOT NULL,
                    PAYMENT_METHOD VARCHAR(20) NOT NULL,
                    COINS_SPENT DECIMAL(10,4) DEFAULT 0,
                    INR_PAID INT DEFAULT 0,
                    RAZORPAY_ORDER_ID VARCHAR(64) DEFAULT NULL,
                    RAZORPAY_PAYMENT_ID VARCHAR(64) DEFAULT NULL,
                    STATUS VARCHAR(20) DEFAULT 'PENDING',
                    SHIPPING_NAME VARCHAR(80) DEFAULT '',
                    SHIPPING_ADDRESS TEXT,
                    SHIPPING_PHONE VARCHAR(20) DEFAULT '',
                    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (USER_ID) REFERENCES NAMES(USER_ID) ON DELETE CASCADE
                ) ENGINE=InnoDB
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS STORE_SETTINGS (
                    SKEY VARCHAR(60) PRIMARY KEY,
                    SVAL TEXT NOT NULL,
                    UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
            """)
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
            for key, val in [
                ("coin_to_inr",          "10000"),
                ("service_charge_inr",   "500"),
                ("service_charge_coins", "0.05"),
                ("store_name",           "NOVA WATCHES"),
                ("razorpay_enabled",     "1"),
                ("coins_enabled",        "1"),
            ]:
                cur.execute(
                    "INSERT IGNORE INTO STORE_SETTINGS (SKEY, SVAL) VALUES (%s, %s)",
                    (key, val),
                )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _add_column_if_missing(cur, table: str, column: str, definition: str) -> None:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except Exception:
            pass


# =============================================================================
# QR MANAGER
# =============================================================================

class QRManager:
    """Fernet-encrypted QR code generation and verification."""

    def __init__(self, fernet_key: str) -> None:
        key = fernet_key.encode() if isinstance(fernet_key, str) else fernet_key
        self._cipher = Fernet(key)

    def generate_payload(self, code_name: str, coin: str) -> str:
        timestamp = int(time.time())
        nonce     = uuid.uuid4().hex[:8]
        raw       = f"{code_name}|{coin}|{timestamp}|{nonce}"
        token     = hashlib.sha256(raw.encode()).hexdigest()[:8]
        payload   = {"c": code_name, "co": coin, "t": timestamp, "n": nonce, "tk": token}
        encrypted = self._cipher.encrypt(
            json.dumps(payload, separators=(",", ":")).encode()
        )
        return base64.urlsafe_b64encode(encrypted).decode()

    def decrypt_payload(self, qr_string: str) -> Optional[dict]:
        try:
            decoded   = base64.urlsafe_b64decode(qr_string)
            decrypted = self._cipher.decrypt(decoded)
            data      = json.loads(decrypted)
            if int(time.time()) - int(data["t"]) > 60:
                return None
            raw      = f"{data['c']}|{data['co']}|{data['t']}|{data['n']}"
            expected = hashlib.sha256(raw.encode()).hexdigest()[:8]
            if data.get("tk") != expected:
                return None
            return {"code_name": data["c"], "coin": data["co"],
                    "timestamp": data["t"], "nonce": data["n"], "token": data["tk"]}
        except Exception as exc:
            logging.warning("[QR Decrypt] %s: %s", type(exc).__name__, exc)
            return None


# =============================================================================
# ANTI-CHEAT VALIDATOR
# =============================================================================

class RunValidator:
    """Server-side GPS track validation."""

    @staticmethod
    def _haversine_km(lat1, lon1, lat2, lon2) -> float:
        R    = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a    = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlon / 2) ** 2
        )
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _ist_time(unix_ts: float) -> dtime:
        return datetime.utcfromtimestamp(unix_ts + IST_OFFSET_SEC).time()

    @classmethod
    def validate(cls, points: list) -> tuple:
        if not points or len(points) < 2:
            return 0.0, "Minimum 2 GPS points required"
        try:
            pts = sorted(points, key=lambda p: float(p["timestamp"]))
        except (KeyError, TypeError, ValueError):
            return 0.0, "Invalid point data: timestamp missing"

        first_time = cls._ist_time(float(pts[0]["timestamp"]))
        if first_time >= dtime(RUN_WINDOW_START_HOUR, 0, 0):
            return 0.0, (
                f"Register BEFORE {RUN_WINDOW_START_HOUR}:00 AM IST. "
                f"First point was {first_time.strftime('%H:%M:%S')} IST."
            )

        total_km: float     = 0.0
        segment_speeds: list = []

        for i in range(1, len(pts)):
            prev, curr = pts[i - 1], pts[i]
            try:
                lat1, lon1 = float(prev["lat"]), float(prev["lon"])
                lat2, lon2 = float(curr["lat"]), float(curr["lon"])
                t1, t2     = float(prev["timestamp"]), float(curr["timestamp"])
            except (KeyError, TypeError, ValueError):
                return 0.0, f"Invalid GPS data at point {i}"

            pt_time = cls._ist_time(t2)
            if not (dtime(RUN_WINDOW_START_HOUR, 0, 0) <= pt_time <= dtime(RUN_WINDOW_END_HOUR, 0, 0)):
                return 0.0, (
                    f"Point {i} outside window "
                    f"({RUN_WINDOW_START_HOUR}:00–{RUN_WINDOW_END_HOUR}:00 AM IST). "
                    f"Got {pt_time.strftime('%H:%M:%S')} IST."
                )

            acc = curr.get("accuracy_m")
            if acc is not None:
                try:
                    if float(acc) > MAX_GPS_ACCURACY_METERS:
                        return 0.0, f"GPS accuracy too low at point {i} ({acc}m)."
                except (TypeError, ValueError):
                    pass

            dt_hours = (t2 - t1) / 3600.0
            if dt_hours <= 0:
                return 0.0, "Timestamps must be strictly increasing"

            seg_km    = cls._haversine_km(lat1, lon1, lat2, lon2)
            speed_kmh = seg_km / dt_hours

            if speed_kmh > MAX_RUNNING_SPEED_KMH:
                return 0.0, (
                    f"Speed too high ({speed_kmh:.1f} km/h) at segment {i}. "
                    f"Max: {MAX_RUNNING_SPEED_KMH} km/h."
                )
            if speed_kmh >= MIN_RUNNING_SPEED_KMH:
                total_km += seg_km
                segment_speeds.append(speed_kmh)

        if len(segment_speeds) >= 5:
            mean = sum(segment_speeds) / len(segment_speeds)
            std  = math.sqrt(
                sum((s - mean) ** 2 for s in segment_speeds) / len(segment_speeds)
            )
            if std < SPEED_VARIANCE_MIN_KMPH:
                return 0.0, (
                    f"Speed too constant (std-dev {std:.2f} km/h). "
                    f"Minimum: {SPEED_VARIANCE_MIN_KMPH} km/h."
                )
        return total_km, None


# =============================================================================
# OTP SERVICE
# =============================================================================

# class OTPService:
#     """OTP generation, storage, verification, and email delivery."""

#     # ── Configuration ──────────────────────────────────────────────
#     LOGO_PATH = os.path.join(os.path.dirname(__file__), "images", "logo.png")
#     # ───────────────────────────────────────────────────────────────

#     @staticmethod
#     def generate() -> str:
#         return str(random.randint(100000, 999999))

#     @staticmethod
#     def store(target: str, otp: str, purpose: str) -> None:
#         conn = DatabaseManager.get_connection()
#         cur  = conn.cursor()
#         try:
#             cur.execute(
#                 "UPDATE OTP_STORE SET USED=1 WHERE TARGET=%s AND PURPOSE=%s AND USED=0",
#                 (target, purpose),
#             )
#             expires = int(time.time()) + OTP_EXPIRY_SEC
#             cur.execute(
#                 "INSERT INTO OTP_STORE (TARGET, OTP_CODE, PURPOSE, EXPIRES_AT) VALUES (%s,%s,%s,%s)",
#                 (target, otp, purpose, expires),
#             )
#             conn.commit()
#         finally:
#             cur.close(); conn.close()

#     @staticmethod
#     def verify(target: str, otp: str, purpose: str) -> bool:
#         conn = DatabaseManager.get_connection()
#         cur  = conn.cursor()
#         try:
#             cur.execute(
#                 """SELECT ID FROM OTP_STORE
#                    WHERE TARGET=%s AND OTP_CODE=%s AND PURPOSE=%s AND USED=0 AND EXPIRES_AT>%s
#                    ORDER BY ID DESC LIMIT 1""",
#                 (target, otp, purpose, int(time.time())),
#             )
#             row = cur.fetchone()
#             if not row:
#                 return False
#             cur.execute("UPDATE OTP_STORE SET USED=1 WHERE ID=%s", (row[0],))
#             conn.commit()
#             return True
#         finally:
#             cur.close(); conn.close()

#     @staticmethod
#     def send_email(email: str, otp: str) -> None:
#         if not Config.SMTP_USER or not Config.SMTP_PASSWORD:
#             print(f"[DEV EMAIL OTP] {email} -> {otp}")
#             return

#         msg            = MIMEMultipart("related")
#         msg["Subject"] = "Nova – Your Authentication Code"
#         msg["From"]    = Config.SMTP_USER
#         msg["To"]      = email

#         html = f"""
#         <html>
#         <body style="margin:0;padding:0;background:#ffffff;">
#           <table width="100%" cellpadding="0" cellspacing="0">
#             <tr>
#               <td align="center" style="padding:40px 20px;">
#                 <table width="560" cellpadding="0" cellspacing="0" style="padding:40px;">
#                   <tr>
#                     <td align="center"
#                         style="font-family:Arial,sans-serif;font-size:20px;
#                                font-weight:bold;letter-spacing:2px;padding-bottom:6px;">
#                       PLEASE VERIFY YOUR IDENTITY
#                     </td>
#                   </tr>

#                   <tr>
#                     <td align="center"
#                         style="font-family:Arial,sans-serif;font-size:12px;
#                                letter-spacing:1px;color:#333;padding-bottom:20px;">
#                       HERE IS YOUR NOVA AUTHENTICATION CODE
#                     </td>
#                   </tr>

#                   <tr>
#                     <td align="center"
#                         style="font-family:'Courier New',monospace;font-size:36px;
#                                font-weight:bold;letter-spacing:6px;padding-bottom:20px;">
#                       {otp}
#                     </td>
#                   </tr>

#                   <tr><td style="border-top:1px solid #000;padding-bottom:20px;"></td></tr>

#                   <tr>
#                     <td style="font-family:'Courier New',monospace;font-size:12px;line-height:1.8;">
#                       This code is valid for 5 minutes and can only be used once.<br/><br/>
#                       Please don't share this code with anyone: we'll never ask
#                       for it on the phone or via email.<br/><br/>
#                       Thanks,<br/>The Nova team
#                     </td>
#                   </tr>

#                   <tr><td style="border-top:1px solid #000;padding:16px 0;"></td></tr>

#                   <tr>
#                     <td align="center"
#                         style="font-family:Arial,sans-serif;font-size:11px;color:#555;">
#                       You're receiving this email because a verification code was requested
#                       for your nova account. If this wasn't you, please ignore this email.
#                     </td>
#                   </tr>

#                 </table>
#               </td>
#             </tr>
#           </table>
#         </body>
#         </html>
#         """

#         msg.attach(MIMEText(html, "html"))

#         with open(OTPService.LOGO_PATH, "rb") as f:
#             logo = MIMEImage(f.read())
#             logo.add_header("Content-ID", "<nova_logo>")
#             logo.add_header("Content-Disposition", "inline")
#             msg.attach(logo)

#         server = smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT)
#         try:
#             server.ehlo()
#             server.starttls()
#             server.ehlo()
#             server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
#             server.send_message(msg)
#         finally:
#             try:
#                 server.quit()
#             except Exception:
#                 pass

class OTPService:
    """OTP generation, storage, verification, and email delivery."""

    @staticmethod
    def generate() -> str:
        return str(random.randint(100000, 999999))

    @staticmethod
    def store(target: str, otp: str, purpose: str) -> None:
        conn = DatabaseManager.get_connection()
        cur  = conn.cursor()
        try:
            cur.execute(
                "UPDATE OTP_STORE SET USED=1 WHERE TARGET=%s AND PURPOSE=%s AND USED=0",
                (target, purpose),
            )
            expires = int(time.time()) + OTP_EXPIRY_SEC
            cur.execute(
                "INSERT INTO OTP_STORE (TARGET, OTP_CODE, PURPOSE, EXPIRES_AT) VALUES (%s,%s,%s,%s)",
                (target, otp, purpose, expires),
            )
            conn.commit()
        finally:
            cur.close(); conn.close()

    @staticmethod
    def verify(target: str, otp: str, purpose: str) -> bool:
        conn = DatabaseManager.get_connection()
        cur  = conn.cursor()
        try:
            cur.execute(
                """SELECT ID FROM OTP_STORE
                   WHERE TARGET=%s AND OTP_CODE=%s AND PURPOSE=%s AND USED=0 AND EXPIRES_AT>%s
                   ORDER BY ID DESC LIMIT 1""",
                (target, otp, purpose, int(time.time())),
            )
            row = cur.fetchone()
            if not row:
                return False
            cur.execute("UPDATE OTP_STORE SET USED=1 WHERE ID=%s", (row[0],))
            conn.commit()
            return True
        finally:
            cur.close(); conn.close()

    @staticmethod
    def send_email(email: str, otp: str) -> None:
        if not Config.SMTP_USER or not Config.SMTP_PASSWORD:
            print(f"[DEV EMAIL OTP] {email} -> {otp}")
            return

        html = f"""
        <html>
        <body style="margin:0;padding:0;background:#ffffff;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td align="center" style="padding:40px 20px;">
                <table width="560" cellpadding="0" cellspacing="0" style="padding:40px;">
                  <tr>
                    <td align="center"
                        style="font-family:Arial,sans-serif;font-size:20px;
                               font-weight:bold;letter-spacing:2px;padding-bottom:6px;">
                      PLEASE VERIFY YOUR IDENTITY
                    </td>
                  </tr>
                  <tr>
                    <td align="center"
                        style="font-family:Arial,sans-serif;font-size:12px;
                               letter-spacing:1px;color:#333;padding-bottom:20px;">
                      HERE IS YOUR NOVA AUTHENTICATION CODE
                    </td>
                  </tr>
                  <tr>
                    <td align="center"
                        style="font-family:'Courier New',monospace;font-size:36px;
                               font-weight:bold;letter-spacing:6px;padding-bottom:20px;">
                      {otp}
                    </td>
                  </tr>
                  <tr><td style="border-top:1px solid #000;padding-bottom:20px;"></td></tr>
                  <tr>
                    <td style="font-family:'Courier New',monospace;font-size:12px;line-height:1.8;">
                      This code is valid for 5 minutes and can only be used once.<br/><br/>
                      Please don't share this code with anyone: we'll never ask
                      for it on the phone or via email.<br/><br/>
                      Thanks,<br/>The Nova team
                    </td>
                  </tr>
                  <tr><td style="border-top:1px solid #000;padding:16px 0;"></td></tr>
                  <tr>
                    <td align="center"
                        style="font-family:Arial,sans-serif;font-size:11px;color:#555;">
                      You're receiving this email because a verification code was requested
                      for your Nova account. If this wasn't you, please ignore this email.
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
          </table>
        </body>
        </html>
        """

        msg            = EmailMessage()
        msg["Subject"] = "Nova – Your Authentication Code"
        msg["From"]    = Config.SMTP_USER
        msg["To"]      = email
        msg.set_content(f"Your Nova OTP is: {otp}. Valid for 5 minutes. Do not share it.")
        msg.add_alternative(html, subtype="html")

        server = smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT)
        try:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass


# =============================================================================
# PAYMENT SERVICE
# =============================================================================

class PaymentService:
    """Razorpay order creation and signature verification."""

    @staticmethod
    def create_order(amount_paise: int, receipt: str) -> dict:
        import urllib.request
        url     = "https://api.razorpay.com/v1/orders"
        payload = json.dumps(
            {"amount": amount_paise, "currency": "INR", "receipt": receipt}
        ).encode()
        creds   = base64.b64encode(
            f"{Config.RAZORPAY_KEY_ID}:{Config.RAZORPAY_KEY_SECRET}".encode()
        ).decode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    @staticmethod
    def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
        # BUG FIX: explicit digestmod=hashlib.sha256 (original was missing)
        msg      = f"{order_id}|{payment_id}".encode()
        expected = hmac.new(
            Config.RAZORPAY_KEY_SECRET.encode(), msg, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)


# =============================================================================
# GOOGLE AUTH SERVICE
# =============================================================================

class GoogleAuthService:
    """Verifies Google ID tokens."""

    @staticmethod
    def verify_token(credential: str) -> dict:
        if not Config.GOOGLE_CLIENT_ID:
            raise ValueError("GOOGLE_CLIENT_ID is not configured.")
        try:
            from google.oauth2 import id_token as _id_token
            from google.auth.transport import requests as _greq
            return _id_token.verify_oauth2_token(
                credential, _greq.Request(), Config.GOOGLE_CLIENT_ID
            )
        except ImportError:
            pass
        # Fallback: manual JWT decode (dev only — no signature verification)
        import base64 as _b64, json as _json
        parts = credential.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT")
        padding = 4 - len(parts[1]) % 4
        decoded = _b64.urlsafe_b64decode(parts[1] + "=" * padding)
        payload = _json.loads(decoded)
        if payload.get("aud") != Config.GOOGLE_CLIENT_ID:
            raise ValueError("Token audience mismatch")
        if payload.get("exp", 0) < int(time.time()):
            raise ValueError("Token expired")
        return payload


# =============================================================================
# STORE SERVICE
# =============================================================================

class StoreService:
    """Helpers for the watch store."""

    @staticmethod
    def is_admin(code_name: str) -> bool:
        return (code_name or "").upper() == Config.STORE_ADMIN_CODE_NAME

    @staticmethod
    def slugify(name: str) -> str:
        s = name.lower().strip()
        s = _re.sub(r"[^a-z0-9]+", "-", s)
        return s.strip("-")

    @staticmethod
    def get_setting(key: str, default: str = "") -> str:
        try:
            conn = DatabaseManager.get_connection()
            cur  = conn.cursor()
            cur.execute("SELECT SVAL FROM STORE_SETTINGS WHERE SKEY=%s", (key,))
            row = cur.fetchone()
            cur.close(); conn.close()
            return row[0] if row else default
        except Exception:
            return default


# =============================================================================
# SHARED HELPERS
# =============================================================================

def _generate_coin() -> str:
    """Generate a 10-digit numeric coin string (BUG FIX: typo genearte_coin)."""
    return "".join(str(random.randint(1, 90)) for _ in range(10))


def _fetch_coin_row(cur, user_id: str) -> Optional[tuple]:
    cur.execute("SELECT COIN, COINVALUE FROM SZEROS WHERE USER_ID = %s", (user_id,))
    row = cur.fetchone()
    return (row[0], int(row[1])) if row else None


def _fetch_user_by_code_name(cur, code_name: str) -> Optional[tuple]:
    cur.execute(
        "SELECT USER_ID, CODE_NAME FROM NAMES WHERE UPPER(CODE_NAME) = %s", (code_name,)
    )
    row = cur.fetchone()
    return (row[0], row[1]) if row else None


def _require_login() -> tuple:
    """Return (user_id, code_name) or raise PermissionError."""
    if "code_name" not in session or "user_id" not in session:
        raise PermissionError("Not logged in")
    return session["user_id"], session["code_name"]


# =============================================================================
# FLASK APP
# =============================================================================

app = Flask(__name__, static_folder="static")
app.secret_key = Config.SESSION_SECRET

qr_manager = QRManager(Config.FERNET_KEY)


@app.before_request
def _auto_init_db():
    try:
        DatabaseManager.ensure_tables_once()
    except Exception as exc:
        app.logger.error("DB init failed: %s", exc)


# ── Static routes ─────────────────────────────────────────────────────────────

@app.get("/")
def home():
    resp = send_file(os.path.join(app.root_path, "index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"]        = "no-cache"
    return resp


@app.get("/media/<path:filename>")
def media(filename: str):
    return send_from_directory(
        os.path.join(app.root_path, "images"), filename, conditional=True
    )


@app.get("/images/<path:filename>")
def images(filename: str):
    return send_from_directory(os.path.join(app.root_path, "images"), filename)


# ── Public config ─────────────────────────────────────────────────────────────

@app.get("/api/config")
def api_config():
    return jsonify({
        "google_client_id": Config.GOOGLE_CLIENT_ID,
        "razorpay_enabled": bool(Config.RAZORPAY_KEY_ID),
    })


# ── Activity feed ─────────────────────────────────────────────────────────────

@app.get("/api/activity")
def api_activity():
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT PAYER_CODE_NAME, RECEIVER_CODE_NAME, CREATED_AT
               FROM TRANSACTIONS ORDER BY CREATED_AT DESC LIMIT 50"""
        )
        rows = cur.fetchall() or []
        return jsonify({
            "ok": True,
            "activity": [
                {"payer_code_name": r[0], "receiver_code_name": r[1], "created_at": str(r[2])}
                for r in rows
            ],
        })
    finally:
        cur.close(); conn.close()


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/api/register")
def api_register():
    payload   = request.get_json(force=True) or {}
    full_name = (payload.get("name")      or "").strip()
    email     = (payload.get("email")     or "").strip().lower()
    phone     = (payload.get("phone")     or "").strip()
    code_name = (payload.get("code_name") or "").strip().upper()
    password  = payload.get("password")  or ""
    google_id = (payload.get("google_id") or "").strip() or None
    user_id   = (payload.get("user_id")   or "").strip().upper()

    if not code_name or not (5 <= len(code_name) <= 7):
        return jsonify({"error": "code_name length invalid (expected 5–7 chars)"}), 400
    if not password or not (12 <= len(password) <= 16):
        return jsonify({"error": "password length invalid (expected 12–16 chars)"}), 400
    if not user_id:
        user_id = "".join(str(random.randint(0, 9)) for _ in range(8))

    email_verified = 0
    phone_verified = 0
    if email:
        c = DatabaseManager.get_connection(); cx = c.cursor()
        cx.execute(
            "SELECT 1 FROM OTP_STORE WHERE TARGET=%s AND PURPOSE='email' AND USED=1 LIMIT 1",
            (email,),
        )
        if cx.fetchone(): email_verified = 1
        cx.close(); c.close()
    if phone:
        c = DatabaseManager.get_connection(); cx = c.cursor()
        cx.execute(
            "SELECT 1 FROM OTP_STORE WHERE TARGET=%s AND PURPOSE='phone' AND USED=1 LIMIT 1",
            (phone,),
        )
        if cx.fetchone(): phone_verified = 1
        cx.close(); c.close()

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
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
               (USER_ID, CODE_NAME, PASS_KEY, FULL_NAME, EMAIL, PHONE, GOOGLE_ID,
                EMAIL_VERIFIED, PHONE_VERIFIED)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (user_id, code_name, password, full_name, email, phone,
             google_id, email_verified, phone_verified),
        )

        coin = None
        for _ in range(5):
            candidate = _generate_coin()
            try:
                cur.execute(
                    "INSERT INTO SZEROS (USER_ID, COIN, COINVALUE) VALUES (%s,%s,%s)",
                    (user_id, candidate, 0),
                )
                coin = candidate
                break
            except mysql.connector.Error:
                continue
        if coin is None:
            return jsonify({"error": "failed to allocate unique coin"}), 500

        conn.commit()
        return jsonify({"ok": True, "user_id": user_id, "code_name": code_name, "coin": coin})
    except mysql.connector.Error as exc:
        conn.rollback()
        return jsonify({"error": f"DB error: {exc}"}), 500
    finally:
        cur.close(); conn.close()


@app.post("/api/login")
def api_login():
    payload   = request.get_json(force=True) or {}
    code_name = (payload.get("code_name") or "").strip().upper()
    password  = payload.get("password") or ""

    if not code_name or not password:
        return jsonify({"error": "missing fields"}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT USER_ID FROM NAMES WHERE UPPER(CODE_NAME)=%s AND PASS_KEY=%s",
            (code_name, password),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "invalid credentials"}), 401
        user_id  = row[0]
        coin_row = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "coin account missing"}), 500
        coin, coinvalue = coin_row
        session["code_name"] = code_name
        session["user_id"]   = user_id
        return jsonify({"ok": True, "code_name": code_name, "user_id": user_id,
                        "coin": coin, "coinvalue": coinvalue})
    finally:
        cur.close(); conn.close()


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def api_me():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        coin_row = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "coin account missing"}), 500
        coin, coinvalue = coin_row
        return jsonify({"ok": True, "code_name": code_name, "user_id": user_id,
                        "coin": coin, "coinvalue": coinvalue})
    finally:
        cur.close(); conn.close()


@app.get("/api/account/search")
def api_account_search():
    code_name = (request.args.get("code_name") or "").strip().upper()
    if not code_name:
        return jsonify({"error": "code_name required"}), 400
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        user = _fetch_user_by_code_name(cur, code_name)
        if not user:
            return jsonify({"error": "account not found"}), 404
        user_id, _ = user
        coin_row   = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "coin account missing"}), 500
        coin, coinvalue = coin_row
        return jsonify({"ok": True, "user_id": user_id, "code_name": code_name,
                        "coin": coin, "coinvalue": coinvalue})
    finally:
        cur.close(); conn.close()


@app.get("/api/transactions")
def api_transactions():
    # BUG FIX: _require_login() was called twice — removed duplicate call
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT RECEIVER_CODE_NAME, PAYER_CODE_NAME, AMOUNT, CREATED_AT
               FROM TRANSACTIONS
               WHERE PAYER_CODE_NAME=%s OR RECEIVER_CODE_NAME=%s
               ORDER BY CREATED_AT DESC LIMIT 40""",
            (code_name, code_name),
        )
        rows = cur.fetchall() or []
        return jsonify({
            "ok": True,
            "transactions": [
                {"receiver_code_name": r[0], "payer_code_name": r[1],
                 "amount": int(r[2]), "created_at": str(r[3])}
                for r in rows
            ],
        })
    finally:
        cur.close(); conn.close()


# ── QR ────────────────────────────────────────────────────────────────────────

@app.get("/api/qr/mine")
def api_qr_mine():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        coin_row = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "coin account missing"}), 500
        coin, _ = coin_row
        qr_text = qr_manager.generate_payload(code_name=code_name, coin=str(coin))
        return jsonify({"ok": True, "qrText": qr_text})
    finally:
        cur.close(); conn.close()


@app.post("/api/qr/verify")
def api_qr_verify():
    payload = request.get_json(force=True) or {}
    qr_text = (payload.get("qrText") or "").strip()
    if not qr_text:
        return jsonify({"error": "qrText required"}), 400
    data = qr_manager.decrypt_payload(qr_text)
    if not data:
        return jsonify({"error": "invalid or expired QR"}), 400
    return jsonify({
        "ok": True,
        "receiver_code_name": str(data["code_name"]).upper(),
        "receiver_coin":      str(data["coin"]),
        "timestamp":          int(data["timestamp"]),
    })


# ── Transfer ──────────────────────────────────────────────────────────────────

def _pay_transfer(conn, payer_code_name, payer_password, amount, receiver_code_name, receiver_coin) -> dict:
    cur = conn.cursor()
    try:
        if amount <= 0:
            raise ValueError("amount must be positive")
        cur.execute(
            "SELECT USER_ID FROM NAMES WHERE UPPER(CODE_NAME)=%s AND PASS_KEY=%s",
            (payer_code_name, payer_password),
        )
        row = cur.fetchone()
        if not row:
            raise PermissionError("invalid payer password")
        payer_user_id = row[0]

        cur.execute(
            "SELECT COIN, COINVALUE FROM SZEROS WHERE USER_ID=%s FOR UPDATE", (payer_user_id,)
        )
        prow = cur.fetchone()
        if not prow:
            raise RuntimeError("payer coin account missing")
        payer_coin_db, payer_balance = prow[0], int(prow[1])
        if payer_balance < amount:
            raise ValueError("insufficient balance")

        cur.execute(
            "SELECT USER_ID FROM NAMES WHERE UPPER(CODE_NAME)=%s", (receiver_code_name,)
        )
        rrow = cur.fetchone()
        if not rrow:
            raise LookupError("receiver account not found")
        receiver_user_id = rrow[0]

        cur.execute(
            "SELECT COIN, COINVALUE FROM SZEROS WHERE USER_ID=%s FOR UPDATE", (receiver_user_id,)
        )
        rrrow = cur.fetchone()
        if not rrrow:
            raise RuntimeError("receiver coin account missing")
        receiver_coin_db, receiver_balance = rrrow[0], int(rrrow[1])
        if str(receiver_coin_db) != str(receiver_coin):
            raise ValueError("receiver coin mismatch")

        cur.execute("UPDATE SZEROS SET COINVALUE=COINVALUE-%s WHERE USER_ID=%s", (amount, payer_user_id))
        cur.execute("UPDATE SZEROS SET COINVALUE=COINVALUE+%s WHERE USER_ID=%s", (amount, receiver_user_id))
        cur.execute(
            """INSERT INTO TRANSACTIONS
               (PAYER_CODE_NAME,RECEIVER_CODE_NAME,PAYER_COIN,RECEIVER_COIN,AMOUNT)
               VALUES(%s,%s,%s,%s,%s)""",
            (payer_code_name, receiver_code_name, str(payer_coin_db), str(receiver_coin_db), amount),
        )
        conn.commit()
        return {"ok": True, "payer_balance": payer_balance - amount,
                "receiver_balance": receiver_balance + amount}
    finally:
        cur.close()


@app.post("/api/transaction/pay")
def api_transaction_pay():
    try:
        payer_user_id, payer_code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    payload            = request.get_json(force=True) or {}
    amount             = payload.get("amount")
    payer_password     = payload.get("payer_password")
    receiver_code_name = payload.get("receiver_code_name")
    receiver_coin      = payload.get("receiver_coin")
    qr_text            = payload.get("qrText")

    try:
        amount = int(amount)
    except Exception:
        return jsonify({"error": "amount must be an integer"}), 400
    if not payer_password:
        return jsonify({"error": "payer_password required"}), 400

    if qr_text:
        data = qr_manager.decrypt_payload(str(qr_text).strip())
        if not data:
            return jsonify({"error": "invalid or expired QR"}), 400
        receiver_code_name = str(data["code_name"]).upper()
        receiver_coin      = str(data["coin"]).strip()

    if not receiver_code_name or not receiver_coin:
        return jsonify({"error": "receiver info required"}), 400

    conn = DatabaseManager.get_connection()
    try:
        result = _pay_transfer(
            conn, payer_code_name, str(payer_password), amount,
            str(receiver_code_name).strip().upper(), str(receiver_coin).strip(),
        )
        return jsonify(result)
    except (PermissionError, ValueError, LookupError) as exc:
        conn.rollback()
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": f"payment failed: {exc}"}), 500
    finally:
        conn.close()


# ── Run / Earn ────────────────────────────────────────────────────────────────

@app.post("/api/run/earn")
def api_run_earn():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    payload = request.get_json(force=True) or {}
    points  = payload.get("gps_points")
    if not points or not isinstance(points, list):
        return jsonify({"error": "gps_points array required"}), 400
    if len(points) > 10000:
        return jsonify({"error": "Too many GPS points (max 10000)"}), 400

    valid_km, err = RunValidator.validate(points)
    if err:
        return jsonify({"error": err}), 400

    if valid_km < MIN_SESSION_KM:
        return jsonify({
            "ok": True, "coins_earned": 0, "distance_km": round(valid_km, 3),
            "message": (
                f"You ran {valid_km:.2f} km. "
                f"Need {MIN_SESSION_KM - valid_km:.2f} more km for 1 coin. "
                "Distance does NOT carry forward."
            ),
        }), 200

    coins_to_award = int(valid_km / MIN_SESSION_KM) * COINS_PER_10KM
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT COALESCE(SUM(COINS_EARNED),0) FROM RUN_SESSIONS
               WHERE USER_ID=%s AND DATE(CREATED_AT)=CURDATE()""",
            (user_id,),
        )
        row         = cur.fetchone()
        coins_today = int(row[0]) if row else 0
        if coins_today >= MAX_COINS_PER_DAY:
            return jsonify({"error": "Already earned today's coin. Come back before 5 AM!"}), 429

        coins_to_award = min(coins_to_award, MAX_COINS_PER_DAY - coins_today)
        cur.execute(
            "INSERT INTO RUN_SESSIONS (USER_ID,DISTANCE_KM,COINS_EARNED) VALUES(%s,%s,%s)",
            (user_id, round(valid_km, 3), coins_to_award),
        )
        cur.execute(
            "UPDATE SZEROS SET COINVALUE=COINVALUE+%s WHERE USER_ID=%s",
            (coins_to_award, user_id),
        )
        conn.commit()
        coin_row    = _fetch_coin_row(cur, user_id)
        new_balance = coin_row[1] if coin_row else 0
        return jsonify({
            "ok": True, "coins_earned": coins_to_award,
            "distance_km": round(valid_km, 3), "new_balance": new_balance,
            "message": f"Congratulations! Earned {coins_to_award} coin for {valid_km:.2f} km.",
        })
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": f"Failed to award coins: {exc}"}), 500
    finally:
        cur.close(); conn.close()


# ── OTP ───────────────────────────────────────────────────────────────────────

@app.post("/api/otp/send-email")
def api_otp_send_email():
    payload = request.get_json(force=True) or {}
    email   = (payload.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    otp = OTPService.generate()
    OTPService.store(email, otp, "email")
    try:
        OTPService.send_email(email, otp)
    except Exception as exc:
        app.logger.error("Email OTP send error: %s", exc)
        return jsonify({"error": f"Failed to send OTP: {exc}"}), 500
    return jsonify({"ok": True, "message": "OTP sent to email"})


@app.post("/api/otp/verify-email")
def api_otp_verify_email():
    payload = request.get_json(force=True) or {}
    email   = (payload.get("email") or "").strip().lower()
    otp     = (payload.get("otp")   or "").strip()
    if not email or not otp:
        return jsonify({"error": "email and otp required"}), 400
    if not OTPService.verify(email, otp, "email"):
        return jsonify({"error": "Invalid or expired OTP"}), 400
    return jsonify({"ok": True})


# ── Google login ──────────────────────────────────────────────────────────────

@app.post("/api/google-login")
def api_google_login():
    payload    = request.get_json(force=True) or {}
    credential = (payload.get("credential") or "").strip()
    if not credential:
        return jsonify({"error": "Google credential required"}), 400
    try:
        idinfo = GoogleAuthService.verify_token(credential)
    except Exception as exc:
        return jsonify({"error": f"Google token verification failed: {exc}"}), 401

    google_id = idinfo.get("sub", "")
    email     = (idinfo.get("email") or "").lower()
    name      = idinfo.get("name", "")
    if not google_id or not email:
        return jsonify({"error": "Incomplete Google profile"}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT USER_ID, CODE_NAME FROM NAMES "
            "WHERE GOOGLE_ID=%s OR (EMAIL=%s AND EMAIL_VERIFIED=1) LIMIT 1",
            (google_id, email),
        )
        row = cur.fetchone()
        if row:
            user_id, code_name = row
            cur.execute(
                "UPDATE NAMES SET GOOGLE_ID=%s WHERE USER_ID=%s AND (GOOGLE_ID IS NULL OR GOOGLE_ID='')",
                (google_id, user_id),
            )
            conn.commit()
            session["code_name"] = code_name
            session["user_id"]   = user_id
            coin_row = _fetch_coin_row(cur, user_id)
            coin, coinvalue = coin_row if coin_row else ("", 0)
            return jsonify({"ok": True, "code_name": code_name, "user_id": user_id,
                            "coin": coin, "coinvalue": coinvalue})
        else:
            return jsonify({"ok": True, "needs_setup": True,
                            "email": email, "name": name, "google_id": google_id})
    finally:
        cur.close(); conn.close()


# ── Razorpay coin purchase ────────────────────────────────────────────────────

@app.post("/api/payment/create-order")
def api_payment_create_order():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    payload = request.get_json(force=True) or {}
    coins   = int(payload.get("coins",  0))
    amount  = int(payload.get("amount", 0))
    if coins <= 0 or amount <= 0:
        return jsonify({"error": "Invalid coins or amount"}), 400

    receipt = f"nova_{user_id}_{int(time.time())}"
    try:
        order = PaymentService.create_order(amount, receipt)
    except Exception as exc:
        app.logger.error("Razorpay create order error: %s", exc)
        return jsonify({"error": f"Payment gateway error: {exc}"}), 502

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO COIN_PURCHASES
               (USER_ID,CODE_NAME,RAZORPAY_ORDER_ID,COINS,AMOUNT_PAISE,STATUS)
               VALUES(%s,%s,%s,%s,%s,'PENDING')""",
            (user_id, code_name, order["id"], coins, amount),
        )
        conn.commit()
    finally:
        cur.close(); conn.close()

    return jsonify({"ok": True, "razorpay_order_id": order["id"],
                    "amount": order["amount"], "currency": order["currency"]})


@app.post("/api/payment/verify")
def api_payment_verify():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    payload    = request.get_json(force=True) or {}
    order_id   = (payload.get("razorpay_order_id")   or "").strip()
    payment_id = (payload.get("razorpay_payment_id") or "").strip()
    signature  = (payload.get("razorpay_signature")  or "").strip()
    coins      = int(payload.get("coins",  0))
    amount     = int(payload.get("amount", 0))

    if not order_id or not payment_id or not signature:
        return jsonify({"error": "Missing payment details"}), 400
    if not PaymentService.verify_signature(order_id, payment_id, signature):
        return jsonify({"error": "Payment signature verification failed"}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT ID, STATUS FROM COIN_PURCHASES WHERE RAZORPAY_ORDER_ID=%s FOR UPDATE",
            (order_id,),
        )
        row = cur.fetchone()
        if row and row[1] == "SUCCESS":
            return jsonify({"error": "Order already processed"}), 409

        invoice_no = f"NOVA-{int(time.time())}-{uuid.uuid4().hex[:6].upper()}"
        cur.execute("UPDATE SZEROS SET COINVALUE=COINVALUE+%s WHERE USER_ID=%s", (coins, user_id))
        cur.execute(
            """UPDATE COIN_PURCHASES
               SET STATUS='SUCCESS', RAZORPAY_PAYMENT_ID=%s, INVOICE_NO=%s
               WHERE RAZORPAY_ORDER_ID=%s AND USER_ID=%s""",
            (payment_id, invoice_no, order_id, user_id),
        )
        if cur.rowcount == 0:
            cur.execute(
                """INSERT INTO COIN_PURCHASES
                   (USER_ID,CODE_NAME,RAZORPAY_ORDER_ID,RAZORPAY_PAYMENT_ID,
                    COINS,AMOUNT_PAISE,STATUS,INVOICE_NO)
                   VALUES(%s,%s,%s,%s,%s,%s,'SUCCESS',%s)""",
                (user_id, code_name, order_id, payment_id, coins, amount, invoice_no),
            )
        conn.commit()
        coin_row    = _fetch_coin_row(cur, user_id)
        new_balance = coin_row[1] if coin_row else 0
        return jsonify({"ok": True, "coins_added": coins,
                        "new_balance": new_balance, "invoice_no": invoice_no})
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": f"Failed to credit coins: {exc}"}), 500
    finally:
        cur.close(); conn.close()


# =============================================================================
# STORE
# =============================================================================

@app.get("/api/store/products")
def api_store_products():
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT ID,NAME,SLUG,PRICE_COINS,PRICE_INR,DESCRIPTION,
                      FEATURES,IMAGE_URL,CATEGORY,STOCK,ACTIVE
               FROM STORE_PRODUCTS WHERE ACTIVE=1 ORDER BY ID DESC"""
        )
        rows = cur.fetchall() or []
        products = [
            {"id": r[0], "name": r[1], "slug": r[2], "price_coins": float(r[3]),
             "price_inr": int(r[4]), "description": r[5] or "", "features": r[6] or "",
             "image_url": r[7] or "", "category": r[8] or "watches",
             "stock": int(r[9]), "active": bool(r[10])}
            for r in rows
        ]
        settings = {
            k: StoreService.get_setting(k)
            for k in ["coin_to_inr", "service_charge_inr", "service_charge_coins",
                      "store_name", "razorpay_enabled", "coins_enabled"]
        }
        return jsonify({"ok": True, "products": products, "settings": settings})
    finally:
        cur.close(); conn.close()


@app.post("/api/store/order/coin")
def api_store_order_coin():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    p          = request.get_json(force=True) or {}
    product_id = int(p.get("product_id", 0))
    password   = p.get("password", "")
    shipping   = p.get("shipping", {})
    if not product_id or not password:
        return jsonify({"error": "product_id and password required"}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT ID,NAME,PRICE_COINS,STOCK FROM STORE_PRODUCTS WHERE ID=%s AND ACTIVE=1", (product_id,))
        prod = cur.fetchone()
        if not prod:
            return jsonify({"error": "Product not found"}), 404
        _, prod_name, price_coins, stock = prod
        price_coins = float(price_coins)
        if stock <= 0:
            return jsonify({"error": "Out of stock"}), 400

        cur.execute("SELECT USER_ID FROM NAMES WHERE USER_ID=%s AND PASS_KEY=%s", (user_id, password))
        if not cur.fetchone():
            return jsonify({"error": "Incorrect password"}), 401

        cur.execute("SELECT COIN, COINVALUE FROM SZEROS WHERE USER_ID=%s FOR UPDATE", (user_id,))
        crow = cur.fetchone()
        if not crow:
            return jsonify({"error": "Coin account missing"}), 500
        balance = float(crow[1])

        svc   = float(StoreService.get_setting("service_charge_coins", "0.05"))
        total = price_coins + svc
        if balance < total:
            return jsonify({"error": f"Insufficient coins. Need {total:.4f}, have {balance:.0f}"}), 400

        new_bal = int(balance - total)
        cur.execute("UPDATE SZEROS SET COINVALUE=%s WHERE USER_ID=%s", (new_bal, user_id))
        cur.execute("UPDATE STORE_PRODUCTS SET STOCK=STOCK-1 WHERE ID=%s", (product_id,))

        order_no = f"NW-{int(time.time())}-{uuid.uuid4().hex[:6].upper()}"
        cur.execute(
            """INSERT INTO STORE_ORDERS
               (ORDER_NO,USER_ID,CODE_NAME,PRODUCT_ID,PRODUCT_NAME,PAYMENT_METHOD,
                COINS_SPENT,STATUS,SHIPPING_NAME,SHIPPING_ADDRESS,SHIPPING_PHONE)
               VALUES(%s,%s,%s,%s,%s,'COIN',%s,'CONFIRMED',%s,%s,%s)""",
            (order_no, user_id, code_name, product_id, prod_name, total,
             shipping.get("name",""), shipping.get("address",""), shipping.get("phone","")),
        )
        conn.commit()
        return jsonify({"ok": True, "order_no": order_no, "coins_spent": total, "new_balance": new_bal})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": str(exc)}), 500
    finally:
        cur.close(); conn.close()


@app.post("/api/store/order/razorpay/create")
def api_store_rz_create():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    p          = request.get_json(force=True) or {}
    product_id = int(p.get("product_id", 0))
    if not product_id:
        return jsonify({"error": "product_id required"}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT ID,NAME,PRICE_INR,STOCK FROM STORE_PRODUCTS WHERE ID=%s AND ACTIVE=1", (product_id,))
        prod = cur.fetchone()
        if not prod:
            return jsonify({"error": "Product not found"}), 404
        _, prod_name, price_inr, stock = prod
        if stock <= 0:
            return jsonify({"error": "Out of stock"}), 400

        svc         = int(StoreService.get_setting("service_charge_inr", "500"))
        total_paise = (int(price_inr) + svc) * 100
        receipt     = f"nw_{user_id}_{product_id}_{int(time.time())}"
        order       = PaymentService.create_order(total_paise, receipt)

        order_no = f"NW-{int(time.time())}-{uuid.uuid4().hex[:6].upper()}"
        cur.execute(
            """INSERT INTO STORE_ORDERS
               (ORDER_NO,USER_ID,CODE_NAME,PRODUCT_ID,PRODUCT_NAME,PAYMENT_METHOD,
                INR_PAID,RAZORPAY_ORDER_ID,STATUS,SHIPPING_NAME,SHIPPING_ADDRESS,SHIPPING_PHONE)
               VALUES(%s,%s,%s,%s,%s,'RAZORPAY',%s,%s,'PENDING','','','')""",
            (order_no, user_id, code_name, product_id, prod_name, total_paise, order["id"]),
        )
        conn.commit()
        return jsonify({"ok": True, "razorpay_order_id": order["id"], "amount": order["amount"],
                        "currency": order["currency"], "order_no": order_no, "product_name": prod_name})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": str(exc)}), 500
    finally:
        cur.close(); conn.close()


@app.post("/api/store/order/razorpay/verify")
def api_store_rz_verify():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    p        = request.get_json(force=True) or {}
    rz_oid   = (p.get("razorpay_order_id")  or "").strip()
    rz_pid   = (p.get("razorpay_payment_id") or "").strip()
    rz_sig   = (p.get("razorpay_signature")  or "").strip()
    order_no = (p.get("order_no")            or "").strip()
    shipping = p.get("shipping", {})

    if not all([rz_oid, rz_pid, rz_sig, order_no]):
        return jsonify({"error": "Missing payment details"}), 400
    if not PaymentService.verify_signature(rz_oid, rz_pid, rz_sig):
        return jsonify({"error": "Signature verification failed"}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT ID,PRODUCT_ID,STATUS FROM STORE_ORDERS WHERE ORDER_NO=%s AND USER_ID=%s FOR UPDATE",
            (order_no, user_id),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Order not found"}), 404
        if row[2] == "CONFIRMED":
            return jsonify({"error": "Already confirmed"}), 409

        cur.execute("UPDATE STORE_PRODUCTS SET STOCK=STOCK-1 WHERE ID=%s AND STOCK>0", (row[1],))
        cur.execute(
            """UPDATE STORE_ORDERS
               SET STATUS='CONFIRMED', RAZORPAY_PAYMENT_ID=%s,
                   SHIPPING_NAME=%s, SHIPPING_ADDRESS=%s, SHIPPING_PHONE=%s
               WHERE ORDER_NO=%s""",
            (rz_pid, shipping.get("name",""), shipping.get("address",""),
             shipping.get("phone",""), order_no),
        )
        conn.commit()
        return jsonify({"ok": True, "order_no": order_no})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": str(exc)}), 500
    finally:
        cur.close(); conn.close()


@app.get("/api/store/my-orders")
def api_store_my_orders():
    try:
        user_id, _ = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT ORDER_NO,PRODUCT_NAME,PAYMENT_METHOD,COINS_SPENT,INR_PAID,STATUS,CREATED_AT
               FROM STORE_ORDERS WHERE USER_ID=%s ORDER BY CREATED_AT DESC LIMIT 30""",
            (user_id,),
        )
        rows = cur.fetchall() or []
        return jsonify({
            "ok": True,
            "orders": [
                {"order_no": r[0], "product_name": r[1], "payment_method": r[2],
                 "coins_spent": float(r[3] or 0), "inr_paid": int(r[4] or 0),
                 "status": r[5], "created_at": str(r[6])}
                for r in rows
            ],
        })
    finally:
        cur.close(); conn.close()


@app.delete("/api/account/delete")
def api_account_delete():
    try:
        user_id, _ = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    p        = request.get_json(force=True) or {}
    password = p.get("password", "")
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT USER_ID FROM NAMES WHERE USER_ID=%s AND PASS_KEY=%s", (user_id, password))
        if not cur.fetchone():
            return jsonify({"error": "Incorrect password"}), 401
        cur.execute("SELECT COINVALUE FROM SZEROS WHERE USER_ID=%s", (user_id,))
        crow = cur.fetchone()
        if crow and int(crow[0]) > 0:
            return jsonify({"error": f"Cannot delete. You have {crow[0]} coins. Transfer them first."}), 400
        cur.execute("DELETE FROM NAMES WHERE USER_ID=%s", (user_id,))
        conn.commit()
        session.clear()
        return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": str(exc)}), 500
    finally:
        cur.close(); conn.close()


# ── Admin ─────────────────────────────────────────────────────────────────────

def _admin_guard():
    user_id, code_name = _require_login()
    if not StoreService.is_admin(code_name):
        raise PermissionError("Forbidden")
    return user_id, code_name


@app.get("/api/admin/products")
def api_admin_products():
    try: _admin_guard()
    except PermissionError as exc: return jsonify({"error": str(exc)}), (403 if str(exc) == "Forbidden" else 401)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT ID,NAME,SLUG,PRICE_COINS,PRICE_INR,DESCRIPTION,FEATURES,IMAGE_URL,CATEGORY,STOCK,ACTIVE FROM STORE_PRODUCTS ORDER BY ID DESC")
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "products": [
            {"id":r[0],"name":r[1],"slug":r[2],"price_coins":float(r[3]),"price_inr":int(r[4]),
             "description":r[5] or "","features":r[6] or "","image_url":r[7] or "",
             "category":r[8] or "watches","stock":int(r[9]),"active":bool(r[10])} for r in rows]})
    finally: cur.close(); conn.close()


@app.post("/api/admin/product")
def api_admin_add_product():
    try: _admin_guard()
    except PermissionError as exc: return jsonify({"error": str(exc)}), (403 if str(exc) == "Forbidden" else 401)
    p = request.get_json(force=True) or {}
    name = (p.get("name") or "").strip()
    if not name: return jsonify({"error": "name required"}), 400
    slug = StoreService.slugify(name)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        base, i = slug, 1
        while True:
            cur.execute("SELECT ID FROM STORE_PRODUCTS WHERE SLUG=%s", (slug,))
            if not cur.fetchone(): break
            slug = f"{base}-{i}"; i += 1
        cur.execute(
            """INSERT INTO STORE_PRODUCTS (NAME,SLUG,PRICE_COINS,PRICE_INR,DESCRIPTION,FEATURES,IMAGE_URL,CATEGORY,STOCK,ACTIVE)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (name, slug, float(p.get("price_coins",1.0)), int(p.get("price_inr",10000)),
             (p.get("description") or "").strip(), (p.get("features") or "").strip(),
             (p.get("image_url") or "").strip(), (p.get("category") or "watches").strip(),
             int(p.get("stock",10)), int(p.get("active",1))),
        )
        conn.commit()
        return jsonify({"ok": True, "slug": slug, "id": cur.lastrowid})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": str(exc)}), 500
    finally: cur.close(); conn.close()


@app.put("/api/admin/product/<int:product_id>")
def api_admin_edit_product(product_id: int):
    try: _admin_guard()
    except PermissionError as exc: return jsonify({"error": str(exc)}), (403 if str(exc) == "Forbidden" else 401)
    p = request.get_json(force=True) or {}
    fields, vals = [], []
    for field, col in [("name","NAME"),("price_coins","PRICE_COINS"),("price_inr","PRICE_INR"),
                       ("description","DESCRIPTION"),("features","FEATURES"),("image_url","IMAGE_URL"),
                       ("category","CATEGORY"),("stock","STOCK"),("active","ACTIVE")]:
        if field in p: fields.append(f"{col}=%s"); vals.append(p[field])
    if not fields: return jsonify({"error": "Nothing to update"}), 400
    vals.append(product_id)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute(f"UPDATE STORE_PRODUCTS SET {', '.join(fields)} WHERE ID=%s", vals)
        conn.commit(); return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": str(exc)}), 500
    finally: cur.close(); conn.close()


@app.delete("/api/admin/product/<int:product_id>")
def api_admin_delete_product(product_id: int):
    try: _admin_guard()
    except PermissionError as exc: return jsonify({"error": str(exc)}), (403 if str(exc) == "Forbidden" else 401)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM STORE_PRODUCTS WHERE ID=%s", (product_id,))
        conn.commit(); return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": str(exc)}), 500
    finally: cur.close(); conn.close()


@app.get("/api/admin/orders")
def api_admin_orders():
    try: _admin_guard()
    except PermissionError as exc: return jsonify({"error": str(exc)}), (403 if str(exc) == "Forbidden" else 401)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT ORDER_NO,CODE_NAME,PRODUCT_NAME,PAYMENT_METHOD,COINS_SPENT,INR_PAID,
                      STATUS,SHIPPING_NAME,SHIPPING_ADDRESS,SHIPPING_PHONE,CREATED_AT
               FROM STORE_ORDERS ORDER BY CREATED_AT DESC LIMIT 300"""
        )
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "orders": [
            {"order_no":r[0],"code_name":r[1],"product_name":r[2],"payment_method":r[3],
             "coins_spent":float(r[4] or 0),"inr_paid":int(r[5] or 0),"status":r[6],
             "shipping_name":r[7],"shipping_address":r[8],"shipping_phone":r[9],
             "created_at":str(r[10])} for r in rows]})
    finally: cur.close(); conn.close()


@app.put("/api/admin/order/<order_no>")
def api_admin_update_order(order_no: str):
    try: _admin_guard()
    except PermissionError as exc: return jsonify({"error": str(exc)}), (403 if str(exc) == "Forbidden" else 401)
    p      = request.get_json(force=True) or {}
    status = (p.get("status") or "").strip().upper()
    if status not in {"PENDING","CONFIRMED","SHIPPED","DELIVERED","CANCELLED"}:
        return jsonify({"error": "Invalid status"}), 400
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("UPDATE STORE_ORDERS SET STATUS=%s WHERE ORDER_NO=%s", (status, order_no))
        conn.commit(); return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": str(exc)}), 500
    finally: cur.close(); conn.close()


@app.get("/api/admin/users")
def api_admin_users():
    try: _admin_guard()
    except PermissionError as exc: return jsonify({"error": str(exc)}), (403 if str(exc) == "Forbidden" else 401)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT n.USER_ID,n.CODE_NAME,n.FULL_NAME,n.EMAIL,n.PHONE,n.CREATED_AT,
                      COALESCE(s.COINVALUE,0) AS BAL
               FROM NAMES n LEFT JOIN SZEROS s ON n.USER_ID=s.USER_ID ORDER BY n.CREATED_AT DESC"""
        )
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "users": [
            {"user_id":r[0],"code_name":r[1],"full_name":r[2],"email":r[3],"phone":r[4],
             "created_at":str(r[5]),"balance":float(r[6])} for r in rows]})
    finally: cur.close(); conn.close()


@app.get("/api/admin/settings")
def api_admin_get_settings():
    try: _admin_guard()
    except PermissionError as exc: return jsonify({"error": str(exc)}), (403 if str(exc) == "Forbidden" else 401)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT SKEY,SVAL FROM STORE_SETTINGS")
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "settings": {r[0]: r[1] for r in rows}})
    finally: cur.close(); conn.close()


@app.post("/api/admin/settings")
def api_admin_save_settings():
    try: _admin_guard()
    except PermissionError as exc: return jsonify({"error": str(exc)}), (403 if str(exc) == "Forbidden" else 401)
    p    = request.get_json(force=True) or {}
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        for k, v in p.items():
            cur.execute(
                "INSERT INTO STORE_SETTINGS (SKEY,SVAL) VALUES(%s,%s) ON DUPLICATE KEY UPDATE SVAL=%s",
                (k, str(v), str(v)),
            )
        conn.commit(); return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": str(exc)}), 500
    finally: cur.close(); conn.close()


# =============================================================================
# ENTRY POINT  (BUG FIX: was placed MID-FILE, splitting module execution)
# =============================================================================

if __name__ == "__main__":
    DatabaseManager.ensure_tables_once()
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG)