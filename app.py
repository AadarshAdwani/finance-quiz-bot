from flask import Flask, jsonify, request, render_template, redirect, url_for, flash
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
import chromadb
from groq import Groq
import os
import fitz
import json
import re
import sqlite3

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "financeiq-secret-key-2024")
CORS(app)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── Flask-Login Setup ──────────────────────────────────────────────────────
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_page"

# ── User Model ─────────────────────────────────────────────────────────────
class User(UserMixin):
    def __init__(self, id, username):
        self.id       = id
        self.username = username

# ── SQLite DB Setup ────────────────────────────────────────────────────────
DB_PATH = "finance_quiz.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    UNIQUE NOT NULL,
            password_hash TEXT    NOT NULL,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_user_by_username(username):
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,))
    row  = c.fetchone()
    conn.close()
    return row

def get_user_by_id(user_id):
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
    row  = c.fetchone()
    conn.close()
    return row

def create_user(username, password_hash):
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
              (username, password_hash))
    conn.commit()
    conn.close()

init_db()

@login_manager.user_loader
def load_user(user_id):
    row = get_user_by_id(user_id)
    if row:
        return User(row[0], row[1])
    return None

# ── Load AI Resources ──────────────────────────────────────────────────────
print("🔄 Loading resources...")
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
chroma_client   = chromadb.PersistentClient(path="./chroma_db")
groq_client     = Groq(api_key=os.getenv("GROQ_API_KEY"))
print("✅ Resources loaded!")

# ── Global Quiz State ──────────────────────────────────────────────────────
current_questions = []

# ── Helper: Extract PDF Text ───────────────────────────────────────────────
def extract_pdf_text(filepath):
    doc  = fitz.open(filepath)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text.strip()

# ── Helper: Chunk Text ─────────────────────────────────────────────────────
def chunk_text(text, chunk_size=300, overlap=50):
    words  = text.split()
    chunks = []
    start  = 0
    while start < len(words):
        chunk = " ".join(words[start:start + chunk_size])
        chunks.append(chunk)
        start = start + chunk_size - overlap
    return chunks

# ── Helper: Store in ChromaDB ──────────────────────────────────────────────
def store_in_chromadb(chunks):
    embeddings = embedding_model.encode(chunks).tolist()
    existing   = [c.name for c in chroma_client.list_collections()]
    if "investment_report" in existing:
        chroma_client.delete_collection("investment_report")
    collection = chroma_client.get_or_create_collection(
        name="investment_report",
        metadata={"hnsw:space": "cosine"}
    )
    collection.add(
        documents  = chunks,
        embeddings = embeddings,
        ids        = [f"chunk_{i}" for i in range(len(chunks))]
    )
    return collection

# ── Helper: Get Context from ChromaDB ─────────────────────────────────────
def get_context(query, collection, n_results=2):
    embedding = embedding_model.encode([query]).tolist()
    results   = collection.query(query_embeddings=embedding, n_results=n_results)
    return "\n".join(results["documents"][0])

# ── Layer 1: Finance Keywords Check ───────────────────────────────────────
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
        "budget", "audit", "valuation report", "investment report"
    ]
    text_lower = text.lower()
    found      = [kw for kw in finance_keywords if kw in text_lower]
    return len(found) >= 5, len(found), found[:8]

# ── Layer 2: AI Finance Validator ──────────────────────────────────────────
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
        temperature = 0.1
    )
    result   = response.choices[0].message.content.strip()
    decision = "NO"
    reason   = "Could not determine document type"
    percent  = "0%"
    for line in result.split('\n'):
        if line.startswith("Decision:"):
            decision = line.replace("Decision:", "").strip().upper()
        elif line.startswith("Reason:"):
            reason   = line.replace("Reason:", "").strip()
        elif line.startswith("Finance percentage:"):
            percent  = line.replace("Finance percentage:", "").strip()
    return decision, reason, percent

# ── Helper: Generate Questions ─────────────────────────────────────────────
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
        temperature = 0.3
    )
    raw   = response.choices[0].message.content.strip()
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        raw = match.group(0)
    return json.loads(raw)

# ── Helper: Evaluate Answer ────────────────────────────────────────────────
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
        temperature = 0.3
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
    return render_template("login.html")

@app.route("/login", methods=["POST"])
def login_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if not username or not password:
        return render_template("login.html",
                               error="Please enter both username and password.",
                               active_tab="login")
    row = get_user_by_username(username)
    if not row or not check_password_hash(row[2], password):
        return render_template("login.html",
                               error="Invalid username or password.",
                               active_tab="login")
    user = User(row[0], row[1])
    login_user(user)
    return redirect(url_for("dashboard"))

@app.route("/register", methods=["POST"])
def register_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    confirm  = request.form.get("confirm_password", "").strip()

    if not username or not password:
        return render_template("login.html",
                               error="Please fill in all fields.",
                               active_tab="register")
    if len(username) < 3:
        return render_template("login.html",
                               error="Username must be at least 3 characters.",
                               active_tab="register")
    if len(password) < 6:
        return render_template("login.html",
                               error="Password must be at least 6 characters.",
                               active_tab="register")
    if password != confirm:
        return render_template("login.html",
                               error="Passwords do not match.",
                               active_tab="register")
    if get_user_by_username(username):
        return render_template("login.html",
                               error="Username already exists. Please choose another.",
                               active_tab="register")

    create_user(username, generate_password_hash(password))
    row  = get_user_by_username(username)
    user = User(row[0], row[1])
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
    return render_template("dashboard.html", username=current_user.username)

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

    file         = request.files["file"]
    num_questions = int(request.form.get("num_questions", 6))

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    if not file.filename.endswith(".pdf"):
        return jsonify({"error": "Only PDF files are allowed"}), 400
    if num_questions < 2 or num_questions > 20:
        return jsonify({"error": "Question count must be between 2 and 20"}), 400

    filepath = os.path.join(UPLOAD_FOLDER, "report.pdf")
    file.save(filepath)

    try:
        print("📄 Extracting PDF text...")
        text = extract_pdf_text(filepath)

        if len(text.strip()) < 100:
            return jsonify({"error": "PDF appears to be empty or unreadable"}), 400

        # Layer 1: Keywords
        print("🔍 Layer 1: Checking finance keywords...")
        kw_passed, kw_count, kw_found = has_finance_keywords(text)
        print(f"   → Found {kw_count} keywords: {kw_found}")

        if not kw_passed:
            return jsonify({
                "error": f"This document does not appear to be finance related. "
                         f"Only {kw_count} finance keywords found (minimum 5 required). "
                         f"Please upload an Investment Report or Financial document."
            }), 400

        # Layer 2: AI validation
        print("🤖 Layer 2: AI finance validation...")
        decision, reason, percent = is_finance_related(text)
        print(f"   → Decision: {decision} | {reason} | {percent}")

        if decision == "NO":
            return jsonify({
                "error": f"This document is not finance related. "
                         f"Detected: {reason}. Please upload a Finance document."
            }), 400

        if decision == "PARTIAL":
            return jsonify({
                "error": f"This document is only partially finance related ({percent}). "
                         f"Please upload a document that is primarily about finance."
            }), 400

        print(f"✅ Finance validation passed! ({percent})")

        # Store in ChromaDB
        print("🗄️ Storing in ChromaDB...")
        chunks = chunk_text(text)
        store_in_chromadb(chunks)

        # Generate questions
        print(f"🤖 Generating {num_questions} questions...")
        current_questions = generate_questions(text, num_questions)
        print(f"✅ Generated {len(current_questions)} questions!")

        return jsonify({
            "success":          True,
            "message":          "PDF processed successfully!",
            "finance_percent":  percent,
            "chunks":           len(chunks),
            "questions":        len(current_questions)
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
            "options":  q["options"]
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
        collection  = chroma_client.get_collection("investment_report")
        context     = get_context(q["question"], collection)
    except Exception:
        context     = "No additional context available."

    explanation = evaluate_answer(
        q["question"], q["options"],
        correct_answer, user_answer, context
    )
    return jsonify({
        "is_correct":     is_correct,
        "correct_answer": correct_answer,
        "explanation":    explanation,
        "topic":          q["topic"]
    })

@app.route("/api/result", methods=["POST"])
@login_required
def get_result():
    data         = request.json
    score        = data.get("score", 0)
    total        = data.get("total", len(current_questions))
    wrong_topics = data.get("wrong_topics", [])
    percentage   = (score / total) * 100

    if percentage >= 80:
        grade   = "Excellent! 🏆"
        message = "Outstanding performance! You have a strong grasp of investment concepts."
    elif percentage >= 60:
        grade   = "Good Job! 👍"
        message = "Good understanding! Review the topics you missed to strengthen your knowledge."
    else:
        grade   = "Keep Studying! 📖"
        message = "Keep practicing! Focus on the topics below to improve your finance knowledge."

    return jsonify({
        "score":        score,
        "total":        total,
        "percentage":   round(percentage, 1),
        "grade":        grade,
        "message":      message,
        "wrong_topics": list(set(wrong_topics))
    })

# ── Run ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, port=5000)