# Deployment Guide

## 1. Run locally first

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export FLASK_DEBUG=1               # Windows PowerShell: $env:FLASK_DEBUG="1"
python app.py
```

Open http://127.0.0.1:5000/doctor. Dev password is `admin123`. Locally the QR uses your LAN IP, so phones on the same Wi-Fi can scan it.

Test file: an Excel/CSV with a header row and a column named `الاسم` or `name`.

## 2. Environment variables

| Variable | Value |
|---|---|
| `SECRET_KEY` | long random string: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DOCTOR_PASSWORD` | your dashboard password |
| `DATABASE_URL` | PostgreSQL URL (recommended for cloud) |
| `COOKIE_SECURE` | `1` (site is HTTPS) |
| `PUBLIC_BASE_URL` | optional, e.g. `https://your-app.onrender.com` |
| `APP_TIMEZONE` | optional, default `Africa/Cairo` |
| `PORT` | set automatically by Render/Railway; never hard-code it |

The app refuses to start in production without `SECRET_KEY` and `DOCTOR_PASSWORD`.

## 3. Render (recommended)

1. Push the folder to a GitHub repo (`app.py`, `templates/`, `static/`, `requirements.txt`, `Procfile` at the repo root).
2. Render dashboard: **New > PostgreSQL**, create it, copy the **Internal Database URL**.
3. **New > Web Service**, connect the repo, then set:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app --workers 3 --threads 8 --timeout 60 --bind 0.0.0.0:$PORT`
   - Health check path: `/healthz`
4. Add the environment variables above (`DATABASE_URL` = the Postgres URL).
5. Deploy, then open `https://<name>.onrender.com/doctor`.

Notes:
- Do not use SQLite on Render: the disk is wiped on every deploy/restart.
- Free web services sleep after inactivity. Open the dashboard 5 minutes before the lecture, or use a paid instance for guaranteed response time.
- Check Render's current free-tier limits for Postgres and instance size, as they change.

## 4. Railway

Same steps: New Project > Deploy from GitHub, add a PostgreSQL plugin, and Railway injects `DATABASE_URL` and `PORT`. The `Procfile` is used automatically. Add `SECRET_KEY`, `DOCTOR_PASSWORD`, `COOKIE_SECURE=1`.

## 5. PythonAnywhere (simplest, but smaller capacity)

1. Upload the files, create a virtualenv, `pip install -r requirements.txt`.
2. Web tab > Add a new web app > Manual configuration. In the WSGI file: `from app import app as application`.
3. Set env variables in the WSGI file (`os.environ[...]`) before the import, including `COOKIE_SECURE=1`.
4. SQLite works here (persistent disk). No gunicorn needed.
5. Free accounts have limited CPU and workers, so test with your class size first.

## 6. Capacity for 300+ students

- Students only make 1 page load, 1-3 small search requests, and 1 write each. Only the doctor's browser polls.
- 3 workers x 8 threads = 24 concurrent requests, enough for 300 students arriving over a minute or two.
- Use PostgreSQL for the cloud. If you must use SQLite, use `--workers 1 --threads 16`.
- If lecture-hall Wi-Fi is poor, students can use mobile data, since the site is on the public internet.

## 7. Test in a real lecture scenario

1. **Day before:** deploy, log in, upload the real roster, start a session, scan the QR with 2-3 phones.
2. **Load test (optional):** `hey -n 300 -c 50 "https://<app>/api/search?q=ahm"` (or use Locust).
3. **Lecture:** log in, upload the roster, click *Start new lecture*, and show the QR on the projector.
4. After about 10 minutes click *Close registration*, then *Export Excel* (sheets: present, absent).
5. Check that the same phone can't register two names, the same name can't register twice, and an old QR is rejected after starting a new lecture.

## 8. Known limits

- One phone per lecture is enforced by a cookie plus browser storage. A student using a private tab or another browser can bypass it.
- The QR proves nothing about location. Students could share a screenshot. Mitigations: close registration soon after starting, or start a new lecture (new QR) mid-class.
- Names are unique keys, so two students with an identical name in the roster count as one. Add a student ID column if this occurs.
- Login has no brute-force rate limit; use a strong `DOCTOR_PASSWORD`.
