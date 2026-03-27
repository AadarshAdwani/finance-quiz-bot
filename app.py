from flask import (
    Flask, jsonify, request, render_template, redirect, url_for,
    flash, session, send_file,
)
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from groq import Groq
import os
import fitz
import json
import re
import sqlite3
import uuid
import secrets
import time
import requests
import jwt
from io import BytesIO
from urllib.parse import urlencode
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from google_auth_oauthlib.flow import Flow
from jwt import PyJWKClient
from fpdf import FPDF
from datetime import datetime, timezone, timedelta

load_dotenv()

# ── IST Timezone ───────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    """Return current UTC datetime string for storage in SQLite."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def format_ist(dt_str):
    """Convert a UTC datetime string from SQLite to IST formatted string."""
    if not dt_str:
        return "Unknown"
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        dt_utc = dt.replace(tzinfo=timezone.utc)
        dt_ist = dt_utc.astimezone(IST)
        return dt_ist.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return dt_str

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "financeiq-secret-key-2024")
CORS(app)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── OAuth (optional — set env vars to enable buttons) ─────────────────────
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
APPLE_CLIENT_ID      = os.getenv("APPLE_CLIENT_ID", "").strip()
APPLE_TEAM_ID        = os.getenv("APPLE_TEAM_ID", "").strip()
APPLE_KEY_ID         = os.getenv("APPLE_KEY_ID", "").strip()
APPLE_PRIVATE_KEY_PATH = os.getenv("APPLE_PRIVATE_KEY_PATH", "").strip()

# ── Flask-Login ────────────────────────────────────────────────────────────
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_page"


class User(UserMixin):
    def __init__(self, id, username, email=None):
        self.id       = id
        self.username = username
        self.email    = email


# ── SQLite ─────────────────────────────────────────────────────────────────
DB_PATH = "finance_quiz.db"


def _conn():
    return sqlite3.connect(DB_PATH)


def migrate_db():
    conn = _conn()
    c    = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            username       TEXT    NOT NULL,
            email          TEXT    UNIQUE,
            phone          TEXT    UNIQUE,
            password_hash  TEXT,
            oauth_provider TEXT,
            oauth_subject  TEXT,
            created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in c.fetchall()}
    for name, ddl in [
        ("email",          "ALTER TABLE users ADD COLUMN email TEXT"),
        ("phone",          "ALTER TABLE users ADD COLUMN phone TEXT"),
        ("oauth_provider", "ALTER TABLE users ADD COLUMN oauth_provider TEXT"),
        ("oauth_subject",  "ALTER TABLE users ADD COLUMN oauth_subject TEXT"),
    ]:
        if name not in cols:
            try:
                c.execute(ddl)
            except sqlite3.OperationalError as e:
                print(f"DB migrate note ({name}): {e}")
    try:
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email "
            "ON users(email) WHERE email IS NOT NULL AND length(trim(email)) > 0"
        )
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone "
            "ON users(phone) WHERE phone IS NOT NULL AND length(trim(phone)) > 0"
        )
    except sqlite3.OperationalError:
        pass
    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_quiz_context (
            user_id            INTEGER PRIMARY KEY,
            stored_pdf_path    TEXT NOT NULL,
            original_filename  TEXT,
            updated_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS quiz_reports (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id            INTEGER NOT NULL,
            created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
            pdf_original_name  TEXT,
            stored_pdf_path    TEXT,
            score              INTEGER NOT NULL,
            total_questions    INTEGER NOT NULL,
            percentage         REAL NOT NULL,
            grade_title        TEXT,
            grade_message      TEXT,
            wrong_topics_json  TEXT,
            answer_detail_json TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    c.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in c.fetchall()}
    if "email" in cols:
        c.execute("SELECT id, username FROM users WHERE email IS NULL OR trim(email) = ''")
        legacy = c.fetchall()
        for uid, uname in legacy:
            gen = f"legacy_{uid}_{re.sub(r'[^a-zA-Z0-9._-]', '_', uname)}@migrate.financeiq.local"
            try:
                c.execute("UPDATE users SET email = ? WHERE id = ?", (gen, uid))
            except sqlite3.IntegrityError:
                c.execute(
                    "UPDATE users SET email = ? WHERE id = ?",
                    (f"legacy_user_{uid}@migrate.financeiq.local", uid),
                )
        conn.commit()
    conn.close()


def init_db():
    migrate_db()


def get_user_row_by_id(user_id):
    conn = _conn()
    c    = conn.cursor()
    c.execute(
        "SELECT id, username, email, phone, password_hash, oauth_provider, oauth_subject "
        "FROM users WHERE id = ?",
        (user_id,),
    )
    row = c.fetchone()
    conn.close()
    return row


def get_user_by_username(username):
    conn = _conn()
    c    = conn.cursor()
    c.execute(
        "SELECT id, username, email, phone, password_hash, oauth_provider, oauth_subject "
        "FROM users WHERE LOWER(username) = LOWER(?)",
        (username.strip(),),
    )
    row = c.fetchone()
    conn.close()
    return row


def get_user_by_email(email):
    if not email:
        return None
    conn = _conn()
    c    = conn.cursor()
    c.execute(
        "SELECT id, username, email, phone, password_hash, oauth_provider, oauth_subject "
        "FROM users WHERE LOWER(email) = LOWER(?)",
        (email.strip(),),
    )
    row = c.fetchone()
    conn.close()
    return row


def get_user_by_phone(phone_digits):
    if not phone_digits or len(phone_digits) < 10:
        return None
    conn = _conn()
    c    = conn.cursor()
    c.execute(
        "SELECT id, username, email, phone, password_hash, oauth_provider, oauth_subject "
        "FROM users WHERE phone = ?",
        (phone_digits,),
    )
    row = c.fetchone()
    conn.close()
    return row


def get_user_by_oauth(provider, subject):
    conn = _conn()
    c    = conn.cursor()
    c.execute(
        "SELECT id, username, email, phone, password_hash, oauth_provider, oauth_subject "
        "FROM users WHERE oauth_provider = ? AND oauth_subject = ?",
        (provider, subject),
    )
    row = c.fetchone()
    conn.close()
    return row


def normalize_phone(raw):
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw.strip())
    return digits if len(digits) >= 10 else None


def create_user(username, email, password_hash, phone=None, oauth_provider=None, oauth_subject=None):
    conn = _conn()
    c    = conn.cursor()
    c.execute(
        "INSERT INTO users (username, email, phone, password_hash, oauth_provider, oauth_subject) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (username, email.lower().strip(), phone, password_hash, oauth_provider, oauth_subject),
    )
    conn.commit()
    uid = c.lastrowid
    conn.close()
    return uid


def upsert_pending_quiz(user_id, pdf_path, original_name):
    conn = _conn()
    c    = conn.cursor()
    c.execute(
        """INSERT INTO pending_quiz_context (user_id, stored_pdf_path, original_filename, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(user_id) DO UPDATE SET
             stored_pdf_path   = excluded.stored_pdf_path,
             original_filename = excluded.original_filename,
             updated_at        = CURRENT_TIMESTAMP""",
        (user_id, pdf_path, original_name),
    )
    conn.commit()
    conn.close()


def get_pending_quiz(user_id):
    conn = _conn()
    c    = conn.cursor()
    c.execute(
        "SELECT stored_pdf_path, original_filename FROM pending_quiz_context WHERE user_id = ?",
        (user_id,),
    )
    row = c.fetchone()
    conn.close()
    return row


def insert_quiz_report(user_id, meta):
    conn = _conn()
    c    = conn.cursor()
    # ── Save IST timestamp instead of UTC ─────────────────────────────
    ist_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        """INSERT INTO quiz_reports (
             user_id, created_at, pdf_original_name, stored_pdf_path, score,
             total_questions, percentage, grade_title, grade_message,
             wrong_topics_json, answer_detail_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            ist_now,
            meta.get("pdf_original_name"),
            meta.get("stored_pdf_path"),
            meta["score"],
            meta["total_questions"],
            meta["percentage"],
            meta.get("grade_title"),
            meta.get("grade_message"),
            json.dumps(meta.get("wrong_topics", [])),
            json.dumps(meta.get("answer_detail", [])),
        ),
    )
    conn.commit()
    rid = c.lastrowid
    conn.close()
    return rid


def list_quiz_reports(user_id, limit=50):
    conn = _conn()
    c    = conn.cursor()
    c.execute(
        """SELECT id, created_at, pdf_original_name, score, total_questions,
                  percentage, grade_title
           FROM quiz_reports WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
        (user_id, limit),
    )
    rows = c.fetchall()
    conn.close()
    # ── Convert each created_at to IST formatted string ───────────────
    formatted = []
    for row in rows:
        row = list(row)
        row[1] = format_ist(row[1])
        formatted.append(tuple(row))
    return formatted


def get_quiz_report(report_id, user_id):
    conn = _conn()
    c    = conn.cursor()
    c.execute(
        """SELECT id, user_id, created_at, pdf_original_name, stored_pdf_path, score,
                  total_questions, percentage, grade_title, grade_message,
                  wrong_topics_json, answer_detail_json
           FROM quiz_reports WHERE id = ? AND user_id = ?""",
        (report_id, user_id),
    )
    row = c.fetchone()
    conn.close()
    return row


def delete_quiz_report(report_id, user_id):
    """Delete a report — only if it belongs to this user."""
    conn = _conn()
    c    = conn.cursor()
    c.execute(
        "DELETE FROM quiz_reports WHERE id = ? AND user_id = ?",
        (report_id, user_id),
    )
    conn.commit()
    deleted = c.rowcount > 0
    conn.close()
    return deleted


init_db()


@login_manager.user_loader
def load_user(user_id):
    row = get_user_row_by_id(int(user_id))
    if row:
        return User(row[0], row[1], row[2])
    return None


def oauth_flags():
    return {
        "google_oauth_enabled": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "apple_oauth_enabled":  bool(
            APPLE_CLIENT_ID and APPLE_TEAM_ID and APPLE_KEY_ID
            and APPLE_PRIVATE_KEY_PATH and os.path.isfile(APPLE_PRIVATE_KEY_PATH)
        ),
    }


def make_google_flow():
    redirect_uri = url_for("auth_google_callback", _external=True)
    return Flow.from_client_config(
        {
            "web": {
                "client_id":     GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                "token_uri":     "https://oauth2.googleapis.com/token",
            }
        },
        scopes=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ],
        redirect_uri=redirect_uri,
    )


def apple_client_secret_jwt():
    if not all([APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_CLIENT_ID, APPLE_PRIVATE_KEY_PATH]):
        return None
    try:
        with open(APPLE_PRIVATE_KEY_PATH, "r", encoding="utf-8") as f:
            key = f.read()
    except OSError:
        return None
    now     = int(time.time())
    headers = {"kid": APPLE_KEY_ID, "alg": "ES256"}
    payload = {
        "iss": APPLE_TEAM_ID,
        "iat": now,
        "exp": now + 86400 * 150,
        "aud": "https://appleid.apple.com",
        "sub": APPLE_CLIENT_ID,
    }
    return jwt.encode(payload, key, algorithm="ES256", headers=headers)


def find_or_create_oauth_user(provider, subject, email_hint, display_name):
    row = get_user_by_oauth(provider, subject)
    if row:
        return User(row[0], row[1], row[2])
    if email_hint:
        existing = get_user_by_email(email_hint)
        if existing and (existing[5] or "").lower() != provider:
            return None
        if existing:
            conn = _conn()
            c    = conn.cursor()
            c.execute(
                "UPDATE users SET oauth_provider = ?, oauth_subject = ? WHERE id = ?",
                (provider, subject, existing[0]),
            )
            conn.commit()
            conn.close()
            return User(existing[0], existing[1], existing[2])
    safe_sub = re.sub(r"[^a-zA-Z0-9._+-]", "_", str(subject))[:56]
    email    = (email_hint or f"{provider}_{safe_sub}@oauth.financeiq.local").lower().strip()
    if get_user_by_email(email) and not email_hint:
        email = f"{provider}_{safe_sub}_{secrets.token_hex(4)}@oauth.financeiq.local"
    uname      = (display_name or email.split("@")[0]).strip()[:80] or "Learner"
    dummy_hash = generate_password_hash(secrets.token_urlsafe(32))
    uid        = create_user(uname, email, dummy_hash, phone=None, oauth_provider=provider, oauth_subject=subject)
    return User(uid, uname, email)


def resolve_login_row(login_id):
    login_id = login_id.strip()
    if "@" in login_id:
        return get_user_by_email(login_id)
    ph = normalize_phone(login_id)
    if ph:
        row = get_user_by_phone(ph)
        if row:
            return row
    return get_user_by_username(login_id)


def pdf_safe_text(text, max_len=5000):
    if text is None:
        return ""
    s = str(text).strip().replace("\r\n", "\n").replace("\r", "\n")
    encoded = s.encode("latin-1", "replace").decode("latin-1")
    return encoded[:max_len]


class _ReportPdf(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, "FinanceIQ - Practice quiz report", align="C")


def build_quiz_result_pdf(report_row):
    (
        _rid, _uid, created_at, pdf_original_name, _stored,
        score, total_questions, percentage, grade_title, grade_message,
        wrong_topics_json, answer_detail_json,
    ) = report_row

    wrong_topics = json.loads(wrong_topics_json or "[]")
    details      = json.loads(answer_detail_json or "[]")

    display_date = format_ist(created_at) if created_at else now_ist()

    pdf = _ReportPdf()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Usable width
    W = pdf.w - pdf.l_margin - pdf.r_margin  # ~170mm on A4

    # ── Header ────────────────────────────────────────────────────────
    pdf.set_fill_color(29, 78, 216)
    pdf.rect(0, 0, pdf.w, 28, "F")
    pdf.set_xy(pdf.l_margin, 7)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(W, 10, "FinanceIQ Quiz Report", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_x(pdf.l_margin)
    pdf.cell(W, 6, f"Generated: {display_date} IST", ln=True)
    pdf.ln(10)

    # ── Meta info ─────────────────────────────────────────────────────
    pdf.set_text_color(10, 22, 40)
    if pdf_original_name:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(W, 6, f"Source document: {pdf_safe_text(pdf_original_name)}", ln=True)
        pdf.ln(2)

    # ── Score box ─────────────────────────────────────────────────────
    pdf.set_fill_color(239, 246, 255)
    pdf.set_draw_color(29, 78, 216)
    box_y = pdf.get_y()
    pdf.rect(pdf.l_margin, box_y, W, 22, "FD")
    pdf.set_xy(pdf.l_margin + 4, box_y + 3)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(29, 78, 216)
    pdf.cell(W - 8, 8, f"Score: {score} / {total_questions}  ({percentage}%)", ln=True)
    pdf.set_x(pdf.l_margin + 4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(10, 22, 40)
    pdf.cell(W - 8, 6, pdf_safe_text(grade_title or ""), ln=True)
    pdf.ln(6)

    # Grade message
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(50, 50, 50)
    pdf.multi_cell(W, 5, pdf_safe_text(grade_message or ""))
    pdf.ln(4)

    # ── Topics to review ──────────────────────────────────────────────
    if wrong_topics:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(10, 22, 40)
        pdf.cell(W, 7, "Topics to Review:", ln=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(60, 60, 60)
        for t in wrong_topics:
            pdf.multi_cell(W, 5, f"  - {pdf_safe_text(t)}")
        pdf.ln(4)

    # ── Divider ───────────────────────────────────────────────────────
    pdf.set_draw_color(200, 210, 230)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + W, pdf.get_y())
    pdf.ln(4)

    # ── Question Summary ──────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(10, 22, 40)
    pdf.cell(W, 8, "Question Summary", ln=True)
    pdf.ln(2)

    for i, d in enumerate(details, 1):
        is_correct = d.get("is_correct", False)

        # Page break check — leave room for at least question block
        if pdf.get_y() > 250:
            pdf.add_page()
            pdf.ln(4)

        # ── Question block background ──────────────────────────────
        if is_correct:
            pdf.set_fill_color(236, 253, 245)   # light green
            pdf.set_draw_color(16, 185, 129)
            status_label = "Correct"
            status_color = (6, 95, 70)
        else:
            pdf.set_fill_color(254, 242, 242)   # light red
            pdf.set_draw_color(239, 68, 68)
            status_label = "Incorrect"
            status_color = (153, 27, 27)

        block_x = pdf.l_margin
        block_y = pdf.get_y()

        # Draw left accent bar
        pdf.set_fill_color(*([16, 185, 129] if is_correct else [239, 68, 68]))
        pdf.rect(block_x, block_y, 3, 6, "F")

        # Q number + status
        pdf.set_xy(block_x + 5, block_y)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*status_color)
        topic = pdf_safe_text(d.get("topic", "General"), 80)
        pdf.cell(W - 5, 6, f"Q{i}  [{status_label}]  -  {topic}", ln=True)
        pdf.ln(1)

        # Question text
        pdf.set_x(block_x + 5)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(10, 22, 40)
        question_text = pdf_safe_text(d.get("question", ""), 2000)
        pdf.multi_cell(W - 5, 5, question_text)
        pdf.ln(2)

        # Options (A, B, C, D) if available
        options = d.get("options", {})
        if options:
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(60, 60, 60)
            for key, val in options.items():
                pdf.set_x(block_x + 8)
                pdf.multi_cell(W - 8, 4, f"{key})  {pdf_safe_text(str(val), 300)}")
            pdf.ln(2)

        # Your answer vs Correct answer
        pdf.set_x(block_x + 5)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(153, 27, 27)
        user_ans = pdf_safe_text(d.get("user_answer", "—"), 300)
        pdf.multi_cell(W - 5, 5, f"Your answer:    {user_ans}")

        pdf.set_x(block_x + 5)
        pdf.set_text_color(6, 95, 70)
        correct_ans = pdf_safe_text(d.get("correct_answer", "—"), 300)
        pdf.multi_cell(W - 5, 5, f"Correct answer: {correct_ans}")
        pdf.ln(2)

        # Explanation
        explanation = pdf_safe_text(d.get("explanation", ""), 2000)
        if explanation:
            pdf.set_x(block_x + 5)
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(80, 80, 80)
            pdf.multi_cell(W - 5, 5, f"Explanation: {explanation}")

        pdf.ln(5)

    return BytesIO(bytes(pdf.output()))


# ── Load AI Resources ──────────────────────────────────────────────────────
print("Loading resources...")
embedding_model = None
chroma_client = None
groq_client     = Groq(api_key=os.getenv("GROQ_API_KEY"))
print("Resources loaded!")

current_questions = []


def extract_pdf_text(filepath):
    doc  = fitz.open(filepath)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text.strip()


def chunk_text(text, chunk_size=300, overlap=50):
    words  = text.split()
    chunks = []
    start  = 0
    while start < len(words):
        chunk = " ".join(words[start:start + chunk_size])
        chunks.append(chunk)
        start = start + chunk_size - overlap
    return chunks


def store_in_chromadb(chunks):
    global chroma_client, embedding_model
    try:
        import chromadb
        from sentence_transformers import SentenceTransformer
        if embedding_model is None:
            embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        if chroma_client is None:
            chroma_client = chromadb.PersistentClient(path="./chroma_db")
        embeddings = embedding_model.encode(chunks).tolist()
        existing   = [c.name for c in chroma_client.list_collections()]
        if "investment_report" in existing:
            chroma_client.delete_collection("investment_report")
        collection = chroma_client.get_or_create_collection(
            name="investment_report",
            metadata={"hnsw:space": "cosine"},
        )
        collection.add(
            documents  = chunks,
            embeddings = embeddings,
            ids        = [f"chunk_{i}" for i in range(len(chunks))],
        )
        return collection
    except Exception as e:
        print(f"ChromaDB skipped (not available): {e}")
        return None

def get_context(query, collection, n_results=2):
    embedding = embedding_model.encode([query]).tolist()
    results   = collection.query(query_embeddings=embedding, n_results=n_results)
    return "\n".join(results["documents"][0])


def has_finance_keywords(text):
    finance_keywords = [
        "investment", "portfolio", "equity", "stock", "bond", "market",
        "securities", "dividend", "index", "fund", "etf", "mutual fund",
        "hedge fund", "derivative", "futures", "options", "commodity",
        "revenue", "profit", "earnings", "ebitda", "valuation", "p/e ratio",
        "return", "yield", "sharpe", "beta", "alpha", "volatility", "vix",
        "interest rate", "inflation", "gdp", "fiscal", "monetary",
        "bank", "federal reserve", "treasury", "asset management",
        "broker", "analyst", "investor", "trader", "portfolio manager",
        "trading", "investing", "allocation", "diversification", "hedging",
        "rebalancing", "compounding", "liquidity", "capital", "debt",
        "financial statement", "balance sheet", "income statement",
        "cash flow", "annual report", "quarterly report", "forecast",
        "budget", "audit", "valuation report", "investment report",
    ]
    text_lower = text.lower()
    found      = [kw for kw in finance_keywords if kw in text_lower]
    return len(found) >= 5, len(found), found[:8]


def is_finance_related(text):
    sample = text[:3000]
    prompt = f"""
You are a document classifier. Analyze the document below and determine
if it is related to Finance, Investment, Economics, or Business.

DOCUMENT:
{sample}

Rules:
- Reply YES if the document is primarily about finance, investment,
  stock markets, economics, banking, trading, budgets, or business reports
- Reply NO if the document is primarily about sports, science,
  history, entertainment, medicine, or any non-finance topic
- Reply PARTIAL if the document has some finance content
  but is mostly about other topics (less than 50% finance)

Respond in this exact format only:
Decision: [YES/NO/PARTIAL]
Reason: [one line reason]
Finance percentage: [estimated % of finance content]
"""
    response = groq_client.chat.completions.create(
        model       = "llama-3.1-8b-instant",
        messages    = [{"role": "user", "content": prompt}],
        temperature = 0.1,
    )
    result   = response.choices[0].message.content.strip()
    decision = "NO"
    reason   = "Could not determine document type"
    percent  = "0%"
    for line in result.split("\n"):
        if line.startswith("Decision:"):
            decision = line.replace("Decision:", "").strip().upper()
        elif line.startswith("Reason:"):
            reason   = line.replace("Reason:", "").strip()
        elif line.startswith("Finance percentage:"):
            percent  = line.replace("Finance percentage:", "").strip()
    return decision, reason, percent


def generate_questions(text, num_questions=6):
    sample = text[:4000]
    prompt = f"""
You are a Finance quiz generator. Based on the investment report below,
generate exactly {num_questions} multiple choice questions.

INVESTMENT REPORT:
{sample}

Rules:
- Each question must be directly based on facts in the report
- Each question must have exactly 4 options (A, B, C, D)
- Only one option must be correct
- Questions should cover different topics in the report
- Keep questions clear and concise

Respond ONLY with a valid JSON array in this exact format, nothing else:
[
  {{
    "question": "Question text here?",
    "topic": "topic name",
    "options": ["A) option1", "B) option2", "C) option3", "D) option4"],
    "answer": "A"
  }}
]
"""
    response = groq_client.chat.completions.create(
        model       = "llama-3.1-8b-instant",
        messages    = [{"role": "user", "content": prompt}],
        temperature = 0.3,
    )
    raw   = response.choices[0].message.content.strip()
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    return json.loads(raw)


def evaluate_answer(question, options, correct_answer, user_answer, context):
    options_text = "\n".join(options)
    prompt = f"""
You are a Finance tutor evaluating a student's quiz answer.

CONTEXT FROM INVESTMENT REPORT:
{context}

QUESTION: {question}
OPTIONS:
{options_text}
CORRECT ANSWER: {correct_answer}
STUDENT ANSWER: {user_answer}

Your job:
1. Say if the student is CORRECT or INCORRECT
2. If INCORRECT, explain WHY the correct answer is right using the context
3. Give a short 2-3 line educational explanation reinforcing the concept
4. Keep the tone encouraging and educational

Respond in this exact format:
Result: [CORRECT/INCORRECT]
Explanation: [your explanation]
Key Concept: [one key takeaway]
"""
    response = groq_client.chat.completions.create(
        model       = "llama-3.1-8b-instant",
        messages    = [{"role": "user", "content": prompt}],
        temperature = 0.3,
    )
    return response.choices[0].message.content


# ══════════════════════════════════════════════════════════════════════════
#  AUTH ROUTES
# ══════════════════════════════════════════════════════════════════════════

@app.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login_page"))


@app.route("/login", methods=["GET"])
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    ctx = oauth_flags()
    return render_template(
        "login.html",
        active_tab=request.args.get("tab", "login"),
        **ctx,
    )


@app.route("/login", methods=["POST"])
def login_post():
    login_id = request.form.get("login_id", "").strip()
    password = request.form.get("password", "").strip()

    if not login_id or not password:
        return render_template(
            "login.html",
            error="Please enter your email, phone, or username and password.",
            active_tab="login",
            **oauth_flags(),
        )
    row = resolve_login_row(login_id)
    if not row or not row[4]:
        return render_template(
            "login.html",
            error="Invalid credentials.",
            active_tab="login",
            **oauth_flags(),
        )
    if not check_password_hash(row[4], password):
        return render_template(
            "login.html",
            error="Invalid credentials.",
            active_tab="login",
            **oauth_flags(),
        )
    user = User(row[0], row[1], row[2])
    login_user(user)
    return redirect(url_for("dashboard"))


@app.route("/register", methods=["POST"])
def register_post():
    username  = request.form.get("username", "").strip()
    email     = request.form.get("email", "").strip()
    phone_raw = request.form.get("phone", "").strip()
    password  = request.form.get("password", "").strip()
    confirm   = request.form.get("confirm_password", "").strip()
    phone     = normalize_phone(phone_raw)

    if not username or not email or not password:
        return render_template(
            "login.html",
            error="Please fill in display name, email, and password.",
            active_tab="register",
            **oauth_flags(),
        )
    if len(username) < 2:
        return render_template(
            "login.html",
            error="Display name must be at least 2 characters.",
            active_tab="register",
            **oauth_flags(),
        )
    if "@" not in email or len(email) < 5:
        return render_template(
            "login.html",
            error="Please enter a valid email address.",
            active_tab="register",
            **oauth_flags(),
        )
    if len(password) < 6:
        return render_template(
            "login.html",
            error="Password must be at least 6 characters.",
            active_tab="register",
            **oauth_flags(),
        )
    if password != confirm:
        return render_template(
            "login.html",
            error="Passwords do not match.",
            active_tab="register",
            **oauth_flags(),
        )
    if get_user_by_email(email):
        return render_template(
            "login.html",
            error="That email is already registered.",
            active_tab="register",
            **oauth_flags(),
        )
    if phone and get_user_by_phone(phone):
        return render_template(
            "login.html",
            error="That mobile number is already registered.",
            active_tab="register",
            **oauth_flags(),
        )

    create_user(
        username, email, generate_password_hash(password),
        phone=phone, oauth_provider=None, oauth_subject=None,
    )
    row  = get_user_by_email(email)
    user = User(row[0], row[1], row[2])
    login_user(user)
    return redirect(url_for("dashboard"))


@app.route("/auth/google")
def auth_google():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        flash("Google sign-in is not configured on this server.")
        return redirect(url_for("login_page"))
    state = secrets.token_urlsafe(32)
    session["google_oauth_state"] = state
    flow = make_google_flow()
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="select_account",
        state=state,
    )
    return redirect(authorization_url)


@app.route("/auth/google/callback")
def auth_google_callback():
    if request.args.get("state") != session.get("google_oauth_state"):
        flash("Sign-in session expired. Please try again.")
        return redirect(url_for("login_page"))
    session.pop("google_oauth_state", None)
    try:
        flow = make_google_flow()
        flow.fetch_token(authorization_response=request.url)
        creds   = flow.credentials
        id_info = google_id_token.verify_oauth2_token(
            creds.id_token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
        email = id_info.get("email")
        name  = id_info.get("name") or (email.split("@")[0] if email else "Learner")
        sub   = id_info.get("sub")
        if not sub:
            flash("Could not verify Google account.")
            return redirect(url_for("login_page"))
        user = find_or_create_oauth_user("google", sub, email, name)
        if not user:
            flash("That email is already used with a different sign-in method.")
            return redirect(url_for("login_page"))
        login_user(user)
        return redirect(url_for("dashboard"))
    except Exception as e:
        print(f"Google OAuth error: {e}")
        flash("Google sign-in failed. Please try again or use email.")
        return redirect(url_for("login_page"))


@app.route("/auth/apple")
def auth_apple():
    if not oauth_flags()["apple_oauth_enabled"]:
        flash("Apple sign-in is not configured on this server.")
        return redirect(url_for("login_page"))
    state        = secrets.token_urlsafe(32)
    session["apple_oauth_state"] = state
    redirect_uri = url_for("auth_apple_callback", _external=True)
    qs = urlencode({
        "response_type": "code",
        "response_mode": "form_post",
        "client_id":     APPLE_CLIENT_ID,
        "redirect_uri":  redirect_uri,
        "scope":         "name email",
        "state":         state,
    })
    return redirect(f"https://appleid.apple.com/auth/authorize?{qs}")


@app.route("/auth/apple/callback", methods=["POST"])
def auth_apple_callback():
    if request.form.get("state") != session.get("apple_oauth_state"):
        flash("Sign-in session expired. Please try again.")
        return redirect(url_for("login_page"))
    session.pop("apple_oauth_state", None)
    code = request.form.get("code")
    if not code:
        flash("Apple did not return an authorization code.")
        return redirect(url_for("login_page"))
    client_secret = apple_client_secret_jwt()
    if not client_secret:
        flash("Apple sign-in is misconfigured.")
        return redirect(url_for("login_page"))
    redirect_uri = url_for("auth_apple_callback", _external=True)
    token_res = requests.post(
        "https://appleid.apple.com/auth/token",
        data={
            "client_id":     APPLE_CLIENT_ID,
            "client_secret": client_secret,
            "code":          code,
            "grant_type":    "authorization_code",
            "redirect_uri":  redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if not token_res.ok:
        flash("Could not complete Apple sign-in.")
        return redirect(url_for("login_page"))
    tokens   = token_res.json()
    id_token = tokens.get("id_token")
    if not id_token:
        flash("Apple response was incomplete.")
        return redirect(url_for("login_page"))
    try:
        jwks_client = PyJWKClient("https://appleid.apple.com/auth/keys")
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        payload     = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=APPLE_CLIENT_ID,
            issuer="https://appleid.apple.com",
        )
    except Exception as e:
        print(f"Apple JWT verify error: {e}")
        flash("Could not verify Apple identity.")
        return redirect(url_for("login_page"))
    sub   = payload.get("sub")
    email = payload.get("email")
    name  = "Learner"
    user_field = request.form.get("user")
    if user_field:
        try:
            uj   = json.loads(user_field)
            name = (uj.get("name") or {}).get("firstName") or name
        except json.JSONDecodeError:
            pass
    if not sub:
        flash("Apple sign-in incomplete.")
        return redirect(url_for("login_page"))
    user = find_or_create_oauth_user("apple", sub, email, name)
    if not user:
        flash("That Apple account could not be linked.")
        return redirect(url_for("login_page"))
    login_user(user)
    return redirect(url_for("dashboard"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login_page"))


@app.route("/dashboard")
@login_required
def dashboard():
    reports = list_quiz_reports(current_user.id)
    return render_template(
        "dashboard.html",
        username=current_user.username,
        email=current_user.email,
        reports=reports,
    )


@app.route("/reports/<int:report_id>/download.pdf")
@login_required
def download_report_pdf(report_id):
    row = get_quiz_report(report_id, current_user.id)
    if not row:
        flash("Report not found.")
        return redirect(url_for("dashboard"))
    buf   = build_quiz_result_pdf(row)
    fname = f"financeiq-report-{report_id}.pdf"
    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=fname,
    )


# ── NEW: Delete Report ─────────────────────────────────────────────────────
@app.route("/reports/<int:report_id>/delete", methods=["POST"])
@login_required
def delete_report(report_id):
    deleted = delete_quiz_report(report_id, current_user.id)
    if not deleted:
        flash("Report not found or already deleted.")
    return redirect(url_for("dashboard"))


# ══════════════════════════════════════════════════════════════════════════
#  QUIZ ROUTES
# ══════════════════════════════════════════════════════════════════════════

@app.route("/quiz")
@login_required
def quiz_page():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
@login_required
def upload_pdf():
    global current_questions

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file          = request.files["file"]
    num_questions = int(request.form.get("num_questions", 6))

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    if not file.filename.endswith(".pdf"):
        return jsonify({"error": "Only PDF files are allowed"}), 400
    if num_questions < 2 or num_questions > 20:
        return jsonify({"error": "Question count must be between 2 and 20"}), 400

    user_dir = os.path.join(UPLOAD_FOLDER, f"user_{current_user.id}")
    os.makedirs(user_dir, exist_ok=True)
    uid_name = f"{uuid.uuid4().hex}.pdf"
    filepath = os.path.join(user_dir, uid_name)
    file.save(filepath)

    try:
        print("Extracting PDF text...")
        text = extract_pdf_text(filepath)

        if len(text.strip()) < 100:
            return jsonify({"error": "PDF appears to be empty or unreadable"}), 400

        print("Layer 1: Checking finance keywords...")
        kw_passed, kw_count, kw_found = has_finance_keywords(text)
        print(f"   Found {kw_count} keywords: {kw_found}")

        if not kw_passed:
            return jsonify({
                "error": (
                    f"This document does not appear to be finance related. "
                    f"Only {kw_count} finance keywords found (minimum 5 required). "
                    f"Please upload an Investment Report or Financial document."
                ),
            }), 400

        print("Layer 2: AI finance validation...")
        decision, reason, percent = is_finance_related(text)
        print(f"   Decision: {decision} | {reason} | {percent}")

        if decision == "NO":
            return jsonify({
                "error": (
                    f"This document is not finance related. "
                    f"Detected: {reason}. Please upload a Finance document."
                ),
            }), 400

        if decision == "PARTIAL":
            return jsonify({
                "error": (
                    f"This document is only partially finance related ({percent}). "
                    f"Please upload a document that is primarily about finance."
                ),
            }), 400

        print(f"Finance validation passed! ({percent})")

        chunks = chunk_text(text)
try:
    print("Storing in ChromaDB...")
    store_in_chromadb(chunks)
except Exception as e:
    print(f"ChromaDB skipped: {e}")

        upsert_pending_quiz(current_user.id, filepath, file.filename)

        print(f"Generating {num_questions} questions...")
        current_questions = generate_questions(text, num_questions)
        print(f"Generated {len(current_questions)} questions!")

        return jsonify({
            "success":         True,
            "message":         "PDF processed successfully!",
            "finance_percent": percent,
            "chunks":          len(chunks),
            "questions":       len(current_questions),
        })

    except json.JSONDecodeError:
        return jsonify({"error": "Failed to generate questions. Please try a different PDF."}), 500
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/questions", methods=["GET"])
@login_required
def get_questions():
    if not current_questions:
        return jsonify({"error": "No questions yet. Please upload a PDF first."}), 400
    questions = []
    for i, q in enumerate(current_questions):
        questions.append({
            "id":       i,
            "question": q["question"],
            "topic":    q["topic"],
            "options":  q["options"],
        })
    return jsonify({"questions": questions})


@app.route("/api/submit", methods=["POST"])
@login_required
def submit_answer():
    data           = request.json
    question_id    = data.get("question_id")
    user_answer    = data.get("answer", "").upper()
    q              = current_questions[question_id]
    correct_answer = q["answer"]
    is_correct     = user_answer == correct_answer

    try:
        collection = chroma_client.get_collection("investment_report")
        context    = get_context(q["question"], collection)
    except Exception:
        context = "No additional context available."

    explanation = evaluate_answer(
        q["question"], q["options"],
        correct_answer, user_answer, context,
    )
    return jsonify({
        "is_correct":     is_correct,
        "correct_answer": correct_answer,
        "explanation":    explanation,
        "topic":          q["topic"],
    })


@app.route("/api/result", methods=["POST"])
@login_required
def get_result():
    data         = request.json
    score        = data.get("score", 0)
    total        = data.get("total", len(current_questions))
    wrong_topics = data.get("wrong_topics", [])
    percentage   = (score / total) * 100 if total else 0

    if percentage >= 80:
        grade   = "Excellent!"
        message = "Outstanding performance! You have a strong grasp of investment concepts."
    elif percentage >= 60:
        grade   = "Good Job!"
        message = "Good understanding! Review the topics you missed to strengthen your knowledge."
    else:
        grade   = "Keep Studying!"
        message = "Keep practicing! Focus on the topics below to improve your finance knowledge."

    return jsonify({
        "score":        score,
        "total":        total,
        "percentage":   round(percentage, 1),
        "grade":        grade,
        "message":      message,
        "wrong_topics": list(set(wrong_topics)),
    })


@app.route("/api/reports/save", methods=["POST"])
@login_required
def save_quiz_report():
    data        = request.json or {}
    pending     = get_pending_quiz(current_user.id)
    stored_path = pending[0] if pending else None
    orig_name   = pending[1] if pending else data.get("pdf_filename")

    try:
        rid = insert_quiz_report(
            current_user.id,
            {
                "pdf_original_name": orig_name or "report.pdf",
                "stored_pdf_path":   stored_path,
                "score":             int(data.get("score", 0)),
                "total_questions":   int(data.get("total", 0)),
                "percentage":        float(data.get("percentage", 0)),
                "grade_title":       data.get("grade"),
                "grade_message":     data.get("message"),
                "wrong_topics":      data.get("wrong_topics", []),
                "answer_detail":     data.get("answer_detail", []),
            },
        )
        return jsonify({"success": True, "report_id": rid})
    except Exception as e:
        print(f"save report: {e}")
        return jsonify({"error": "Could not save report"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)