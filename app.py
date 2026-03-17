from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
import chromadb
from groq import Groq
import os
import fitz  # pymupdf
import json
import re

load_dotenv()

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── Load resources once when server starts ─────────────────────────────────
print("🔄 Loading resources...")
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
chroma_client   = chromadb.PersistentClient(path="./chroma_db")
groq_client     = Groq(api_key=os.getenv("GROQ_API_KEY"))
print("✅ Resources loaded!")

# ── Global quiz state ──────────────────────────────────────────────────────
current_questions = []

# ── Helper: Extract text from PDF ─────────────────────────────────────────
def extract_pdf_text(filepath: str) -> str:
    doc  = fitz.open(filepath)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text.strip()

# ── Helper: Chunk text ─────────────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = 300, overlap: int = 50) -> list:
    words  = text.split()
    chunks = []
    start  = 0
    while start < len(words):
        end   = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start = end - overlap
    return chunks

# ── Helper: Store in ChromaDB ──────────────────────────────────────────────
def store_in_chromadb(chunks: list):
    embeddings = embedding_model.encode(chunks).tolist()

    existing = [c.name for c in chroma_client.list_collections()]
    if "investment_report" in existing:
        chroma_client.delete_collection("investment_report")

    collection = chroma_client.get_or_create_collection(
        name     = "investment_report",
        metadata = {"hnsw:space": "cosine"}
    )
    collection.add(
        documents  = chunks,
        embeddings = embeddings,
        ids        = [f"chunk_{i}" for i in range(len(chunks))]
    )
    return collection

# ── Helper: Get context from ChromaDB ─────────────────────────────────────
def get_context(query: str, collection, n_results: int = 2) -> str:
    embedding = embedding_model.encode([query]).tolist()
    results   = collection.query(query_embeddings=embedding, n_results=n_results)
    return "\n".join(results["documents"][0])

# ── Helper: Generate questions from PDF text using Groq ───────────────────
def generate_questions(text: str) -> list:
    # Use first 4000 chars to avoid token limits
    sample = text[:4000]

    prompt = f"""
You are a Finance quiz generator. Based on the investment report below, generate exactly 6 multiple choice questions.

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

    raw = response.choices[0].message.content.strip()

    # Clean up response - extract JSON array
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        raw = match.group(0)

    questions = json.loads(raw)
    return questions

# ── Helper: Evaluate answer using Groq ────────────────────────────────────
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

# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

# ── Upload PDF ─────────────────────────────────────────────────────────────
@app.route("/api/upload", methods=["POST"])
def upload_pdf():
    global current_questions

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not file.filename.endswith(".pdf"):
        return jsonify({"error": "Only PDF files are allowed"}), 400

    # Save PDF
    filepath = os.path.join(UPLOAD_FOLDER, "report.pdf")
    file.save(filepath)

    try:
        # Extract text
        print("📄 Extracting PDF text...")
        text = extract_pdf_text(filepath)

        if len(text.strip()) < 100:
            return jsonify({"error": "PDF appears to be empty or unreadable"}), 400

        # Store in ChromaDB
        print("🗄️ Storing in ChromaDB...")
        chunks = chunk_text(text)
        store_in_chromadb(chunks)

        # Generate questions
        print("🤖 Generating questions from PDF...")
        current_questions = generate_questions(text)
        print(f"✅ Generated {len(current_questions)} questions!")

        return jsonify({
            "success":  True,
            "message":  f"PDF processed successfully!",
            "pages":    len(text.split('\n')),
            "chunks":   len(chunks),
            "questions": len(current_questions)
        })

    except json.JSONDecodeError:
        return jsonify({"error": "Failed to generate questions. Please try a different PDF."}), 500
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

# ── Get Questions ──────────────────────────────────────────────────────────
@app.route("/api/questions", methods=["GET"])
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

# ── Submit Answer ──────────────────────────────────────────────────────────
@app.route("/api/submit", methods=["POST"])
def submit_answer():
    data           = request.json
    question_id    = data.get("question_id")
    user_answer    = data.get("answer", "").upper()

    q              = current_questions[question_id]
    correct_answer = q["answer"]
    is_correct     = user_answer == correct_answer

    # Get context + AI explanation
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

# ── Get Result ─────────────────────────────────────────────────────────────
@app.route("/api/result", methods=["POST"])
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

# ── Run Server ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, port=5000)