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
