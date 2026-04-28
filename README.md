# Nova

Nova is a secure mobile-first transaction app built with Flask and vanilla JavaScript. Users can send and receive coins via QR code or manual entry. Coins can also be earned by running — 10 km in a single morning session earns 1 coin.

---

## Features

- Register and login with a unique code name and password
- Send coins to other users by scanning their QR code or entering their code name manually
- Earn coins by running 10 km during the morning window (5:00 AM to 8:00 AM IST)
- Real-time GPS tracking with anti-cheat validation
- Live global activity feed showing recent transactions
- Encrypted QR codes with expiry and token verification

---

## Coin Value

1 coin = 10,000 INR

---

## Running and Earning

Rules for earning coins through running:

- You must press Start Run before 5:00 AM IST to register for that day's session
- The active running window is 5:00 AM to 8:00 AM IST
- You must complete exactly 10 km in a single session to earn 1 coin
- Distance does not carry forward between sessions — 9.9 km earns nothing
- Maximum 1 coin per day per user
- Speed must be between 3 km/h and 20 km/h throughout the run
- Speed must show natural variation — constant or robotic pace is rejected
- GPS accuracy must be within 50 meters — indoor or poor signal GPS is rejected

---

## Anti-Cheat Measures

- GPS points are validated server-side independently of the frontend
- Speed per segment is computed using the Haversine formula
- Standard deviation of speed across segments must be at least 1.5 km/h
- QR codes are Fernet-encrypted, time-stamped, and expire after 60 seconds
- Each QR payload includes a SHA-256 token to prevent tampering

---

## Tech Stack

- Backend: Python, Flask
- Database: MySQL (Aiven-compatible, SSL supported)
- Frontend: Vanilla JavaScript, HTML, CSS
- QR Generation: qrcodejs
- QR Scanning: jsQR

---

## Setup

### Requirements

Install dependencies:

```
pip install -r requirements.txt
```

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| DB_HOST | MySQL host | localhost |
| DB_PORT | MySQL port | 3306 |
| DB_USER | MySQL user | root |
| DB_PASSWORD | MySQL password | (set in code) |
| DB_NAME | Database name | user |
| DB_SSL_MODE | SSL mode (REQUIRED / VERIFY_CA) | (none) |
| DB_SSL_CA_PEM | CA certificate PEM content | (none) |
| QR_FERNET_KEY | Fernet key for QR encryption | (default in code) |
| FLASK_SESSION_SECRET | Flask session secret | dev-session-secret-change-me |
| FLASK_DEBUG | Enable debug mode | 1 |
| PORT | Port to listen on | 5000 |

### Run Locally

```
python app.py
```

The app will be available at http://localhost:5000

---

## API Endpoints

### Auth

| Method | Endpoint | Description |
|---|---|---|
| POST | /api/register | Register a new account |
| POST | /api/login | Login |
| POST | /api/logout | Logout |
| GET | /api/me | Get current user info |

### Transactions

| Method | Endpoint | Description |
|---|---|---|
| GET | /api/account/search | Search account by code name |
| GET | /api/transactions | Get transaction history |
| POST | /api/transaction/pay | Send coins to another user |
| GET | /api/activity | Global activity feed |

### QR

| Method | Endpoint | Description |
|---|---|---|
| GET | /api/qr/mine | Generate your encrypted QR payload |
| POST | /api/qr/verify | Verify and decrypt a scanned QR payload |

### Running

| Method | Endpoint | Description |
|---|---|---|
| POST | /api/run/earn | Submit GPS track to earn coins |

---

## Registration Rules

- Code name: 5 to 7 characters
- Password: 12 to 16 characters

---

## Security Notes

- Passwords are stored in plain text in the current version — hashing should be added before production deployment
- The default Fernet key and session secret in the code must be replaced with strong random values in production
- The app is designed for mobile screens only (max width 420px)

---

## Database Tables

| Table | Purpose |
|---|---|
| NAMES | User accounts (user ID, code name, password) |
| SZEROS | Coin wallets (coin number, balance) |
| TRANSACTIONS | Payment history |
| RUN_SESSIONS | Running sessions and coins earned |
