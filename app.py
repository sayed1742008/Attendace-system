"""QR-code attendance system - Menoufia University, Faculty of Electronic Engineering.

Environment variables
---------------------
SECRET_KEY        required in production (same value on every worker)
DOCTOR_PASSWORD   required in production (password for the doctor dashboard)
DATABASE_URL      optional; PostgreSQL URL for production. Defaults to local SQLite.
PUBLIC_BASE_URL   optional; e.g. https://attendance.onrender.com (overrides auto-detection)
COOKIE_SECURE     set to 1 when served over HTTPS
APP_TIMEZONE      default Africa/Cairo
PORT              used only when running `python app.py`
FLASK_DEBUG       set to 1 for local development
"""
import io
import os
import secrets
import socket
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
from flask import (Flask, jsonify, make_response, redirect, render_template,
                   request, send_file, session, url_for)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from werkzeug.middleware.proxy_fix import ProxyFix

DEBUG = os.environ.get("FLASK_DEBUG") == "1"


def env_required(name, dev_default):
    value = os.environ.get(name)
    if value:
        return value
    if DEBUG:
        return dev_default
    raise RuntimeError(f"Environment variable {name} must be set in production")


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
LOCAL_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "Africa/Cairo"))

db_url = os.environ.get("DATABASE_URL") or "sqlite:///" + os.path.join(BASE_DIR, "attendance.db")
if db_url.startswith("postgres://"):  # Render/Heroku style URL -> SQLAlchemy style
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # correct https URLs behind a proxy
app.config.update(
    SECRET_KEY=env_required("SECRET_KEY", "dev-secret-key"),
    SQLALCHEMY_DATABASE_URI=db_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,  # 5 MB upload limit
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)
DOCTOR_PASSWORD = env_required("DOCTOR_PASSWORD", "admin123")

db = SQLAlchemy(app)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):
    """WAL mode lets many readers and one writer work together on SQLite."""
    if dbapi_conn.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False, index=True)


class Lecture(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(200), nullable=False, default="")
    token = db.Column(db.String(32), nullable=False)
    is_open = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lecture_id = db.Column(db.Integer, db.ForeignKey("lecture.id"), nullable=False, index=True)
    student_name = db.Column(db.String(200), nullable=False)
    device_id = db.Column(db.String(64), nullable=False)
    timestamp = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (
        UniqueConstraint("lecture_id", "student_name", name="uq_lecture_student"),
        UniqueConstraint("lecture_id", "device_id", name="uq_lecture_device"),
    )


with app.app_context():
    try:
        db.create_all()
    except Exception:  # another worker created the tables at the same moment
        db.session.rollback()


# --------------------------------------------------------------------------- helpers
def normalize(value):
    return " ".join(str(value or "").split())


def to_local(ts, fmt):
    return ts.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).strftime(fmt)


def new_token():
    return secrets.token_urlsafe(6)


def current_lecture():
    lec = Lecture.query.order_by(Lecture.id.desc()).first()
    if lec is None:
        lec = Lecture(subject="", token=new_token())
        db.session.add(lec)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            lec = Lecture.query.order_by(Lecture.id.desc()).first()
    return lec


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def public_base_url():
    """Cloud URL if deployed; LAN IP if running on localhost (so phones on the same Wi-Fi can connect)."""
    configured = os.environ.get("PUBLIC_BASE_URL")
    if configured:
        return configured.rstrip("/")
    base = request.host_url.rstrip("/")
    parsed = urlparse(base)
    if parsed.hostname in ("localhost", "127.0.0.1"):
        return f"{parsed.scheme}://{get_lan_ip()}:{parsed.port or 80}"
    return base


def doctor_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("doctor"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "غير مصرح، سجّل الدخول أولاً"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------- pages
@app.route("/")
def index():
    return redirect(url_for("doctor_page"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        supplied = (request.form.get("password") or "").encode()
        if secrets.compare_digest(supplied, DOCTOR_PASSWORD.encode()):
            session.clear()
            session.permanent = True
            session["doctor"] = True
            return redirect(url_for("doctor_page"))
        error = "كلمة المرور غير صحيحة"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/doctor")
@doctor_required
def doctor_page():
    return render_template("doctor.html", base_url=public_base_url())


@app.route("/student")
def student_page():
    return render_template("student.html")


@app.route("/healthz")
def healthz():
    return "ok"


# --------------------------------------------------------------------------- doctor API
@app.route("/api/upload_students", methods=["POST"])
@doctor_required
def upload_students():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "لم يتم اختيار ملف"}), 400
    filename = file.filename.lower()
    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(file, encoding="utf-8-sig")
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file, engine="openpyxl")
        else:
            return jsonify({"error": "صيغة الملف غير مدعومة. يرجى رفع ملف Excel أو CSV"}), 400

        if df.empty:
            return jsonify({"error": "الملف فارغ"}), 400

        column = next((c for c in df.columns if "اسم" in str(c) or "name" in str(c).lower()), df.columns[0])
        names = []
        seen = set()
        for value in df[column].dropna():
            name = normalize(value)
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        if not names:
            return jsonify({"error": "لم يتم العثور على أسماء في الملف"}), 400

        Student.query.delete()
        db.session.bulk_save_objects([Student(name=n) for n in names])
        db.session.commit()
        return jsonify({"message": f"تم رفع {len(names)} طالب بنجاح!"})
    except Exception as exc:
        db.session.rollback()
        app.logger.exception("upload failed")
        return jsonify({"error": f"حدث خطأ أثناء معالجة الملف: {exc}"}), 500


@app.route("/api/start_session", methods=["POST"])
@doctor_required
def start_session():
    data = request.get_json(silent=True) or {}
    lec = Lecture(subject=normalize(data.get("subject_name"))[:200], token=new_token())
    db.session.add(lec)
    db.session.commit()
    return jsonify({"message": "تم بدء محاضرة جديدة وتصفير سجل الحضور بنجاح!"})


@app.route("/api/toggle_session", methods=["POST"])
@doctor_required
def toggle_session():
    lec = current_lecture()
    lec.is_open = not lec.is_open
    db.session.commit()
    return jsonify({"is_open": lec.is_open})


@app.route("/api/live_attendance")
@doctor_required
def live_attendance():
    lec = current_lecture()
    records = (Attendance.query.filter_by(lecture_id=lec.id)
               .order_by(Attendance.timestamp.desc()).all())
    return jsonify({
        "token": lec.token,
        "subject": lec.subject,
        "is_open": lec.is_open,
        "total_students": Student.query.count(),
        "attendees": [{"name": r.student_name, "time": to_local(r.timestamp, "%I:%M:%S %p")}
                      for r in records],
    })


@app.route("/api/export_excel")
@doctor_required
def export_excel():
    lec = current_lecture()
    records = (Attendance.query.filter_by(lecture_id=lec.id)
               .order_by(Attendance.timestamp).all())
    present = {r.student_name for r in records}
    present_rows = [{"م": i, "اسم الطالب": r.student_name,
                     "وقت التسجيل": to_local(r.timestamp, "%Y-%m-%d %I:%M:%S %p")}
                    for i, r in enumerate(records, 1)]
    absent_rows = [{"م": i, "اسم الطالب": s.name}
                   for i, s in enumerate(Student.query.order_by(Student.name).all(), 1)
                   if s.name not in present]
    absent_rows = [{**row, "م": i} for i, row in enumerate(absent_rows, 1)]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(present_rows, columns=["م", "اسم الطالب", "وقت التسجيل"]).to_excel(
            writer, index=False, sheet_name="الحضور")
        pd.DataFrame(absent_rows, columns=["م", "اسم الطالب"]).to_excel(
            writer, index=False, sheet_name="الغياب")
        for ws in writer.sheets.values():
            ws.sheet_view.rightToLeft = True
            ws.column_dimensions["B"].width = 45
            ws.column_dimensions["C"].width = 28
    output.seek(0)
    stamp = to_local(utcnow(), "%Y%m%d_%H%M")
    return send_file(output, download_name=f"attendance_{stamp}.xlsx", as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --------------------------------------------------------------------------- student API
@app.route("/api/search")
def search_students():
    """Server-side search so the full class list is never sent to phones."""
    q = normalize(request.args.get("q"))
    if len(q) < 2:
        return jsonify([])
    q = q.replace("%", "").replace("_", "")
    rows = (Student.query.filter(Student.name.ilike(f"%{q}%"))
            .order_by(Student.name).limit(10).all())
    return jsonify([s.name for s in rows])


@app.route("/api/mark_attendance", methods=["POST"])
def mark_attendance():
    data = request.get_json(silent=True) or {}
    name = normalize(data.get("student_name"))
    token = str(data.get("token") or "")
    if not name:
        return jsonify({"error": "بيانات غير مكتملة"}), 400

    lec = current_lecture()
    if token != lec.token:
        return jsonify({"error": "رمز المحاضرة غير صالح أو منتهي. امسح الـ QR Code من جديد."}), 400
    if not lec.is_open:
        return jsonify({"error": "تم إغلاق التسجيل لهذه المحاضرة."}), 400
    if not Student.query.filter_by(name=name).first():
        return jsonify({"error": "هذا الاسم غير موجود في كشف الطلاب."}), 400

    device_id = request.cookies.get("did") or str(data.get("device_uuid") or "")
    device_id = "".join(ch for ch in device_id if ch.isalnum() or ch in "-_")[:64]
    if len(device_id) < 8:
        device_id = secrets.token_hex(16)

    same_device = Attendance.query.filter_by(lecture_id=lec.id, device_id=device_id).first()
    if same_device:
        return jsonify({"error": f"عفواً، هذا الهاتف سجّل حضوراً بالفعل باسم ({same_device.student_name})"}), 400
    if Attendance.query.filter_by(lecture_id=lec.id, student_name=name).first():
        return jsonify({"error": "تم تسجيل حضور هذا الطالب بالفعل!"}), 400

    db.session.add(Attendance(lecture_id=lec.id, student_name=name, device_id=device_id))
    try:
        db.session.commit()
    except IntegrityError:  # two requests raced; the DB constraints decide
        db.session.rollback()
        return jsonify({"error": "تم تسجيل الحضور مسبقاً."}), 400

    resp = make_response(jsonify({"message": f"تم تسجيل حضور الطالب ({name}) بنجاح!"}))
    resp.set_cookie("did", device_id, max_age=365 * 24 * 3600, httponly=True,
                    samesite="Lax", secure=request.is_secure)
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\nLocal:   http://127.0.0.1:{port}/doctor")
    print(f"Network: http://{get_lan_ip()}:{port}/doctor\n")
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
