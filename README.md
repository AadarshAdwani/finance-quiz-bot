# 💰 FinanceIQ — Finance Quiz & Tutor Bot

An AI-powered educational bot for the Finance domain that generates 
quizzes from uploaded Investment Report PDFs.

## 🚀 Features
- Upload any Investment Report PDF
- AI generates 6 quiz questions from the document
- LLM evaluates answers and provides deep explanations
- Performance chart on results screen
- Built with Groq (LLaMA 3), ChromaDB, HuggingFace Embeddings

## 🛠️ Tech Stack
| Layer | Tool |
|---|---|
| Backend | Flask (Python) |
| LLM | Groq API (LLaMA 3.1) |
| Vector DB | ChromaDB |
| Embeddings | HuggingFace sentence-transformers |
| Frontend | HTML + CSS + JavaScript |

## ⚙️ Setup Instructions

### 1. Clone the repository
git clone https://github.com/yourusername/finance_quiz_bot.git
cd finance_quiz_bot

### 2. Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Mac/Linux

### 3. Install dependencies
pip install -r requirements.txt

### 4. Create .env file
Create a `.env` file in the root folder:
GROQ_API_KEY=your_groq_api_key_here

### 5. Run the app
python app.py

### 6. Open browser
http://127.0.0.1:5000
```

Save with **`Ctrl + S`**

---

### ✅ Your folder should now look like:
```
finance_quiz_bot/
├── static/
├── templates/
├── data/
├── venv/          ← will be ignored
├── chroma_db/     ← will be ignored
├── app.py
├── ingest.py
├── quiz_bot.py
├── .env           ← will be ignored
├── .gitignore     ← NEW
├── requirements.txt ← NEW
└── README.md      ← NEW