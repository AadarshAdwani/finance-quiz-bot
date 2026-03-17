import os
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
import chromadb
from groq import Groq
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

load_dotenv()

console = Console()

# ── Pre-built questions from the Investment Report ─────────────────────────
QUIZ_QUESTIONS = [
    {
        "question": "What was the S&P 500 gain in 2024?",
        "topic": "market overview",
        "options": ["A) 10%", "B) 18%", "C) 24%", "D) 7%"],
        "answer": "B"
    },
    {
        "question": "In a balanced portfolio, what percentage should be allocated to fixed income?",
        "topic": "asset allocation",
        "options": ["A) 10%", "B) 60%", "C) 30%", "D) 40%"],
        "answer": "C"
    },
    {
        "question": "What does a portfolio Beta greater than 1 indicate?",
        "topic": "risk analysis",
        "options": [
            "A) Lower volatility than the market",
            "B) No correlation with the market",
            "C) Higher volatility than the market",
            "D) The portfolio is risk-free"
        ],
        "answer": "C"
    },
    {
        "question": "Which sector had the highest return according to the report?",
        "topic": "sector performance",
        "options": ["A) Healthcare", "B) Energy", "C) Financials", "D) Technology"],
        "answer": "D"
    },
    {
        "question": "What happens to bond prices when interest rates rise?",
        "topic": "fixed income",
        "options": [
            "A) Bond prices rise",
            "B) Bond prices fall",
            "C) Bond prices stay the same",
            "D) Bond prices double"
        ],
        "answer": "B"
    },
    {
        "question": "Which type of risk CANNOT be eliminated through diversification?",
        "topic": "investment risks",
        "options": [
            "A) Credit risk",
            "B) Unsystematic risk",
            "C) Sector risk",
            "D) Systematic risk"
        ],
        "answer": "D"
    }
]

# ── 1. Load embedding model & ChromaDB ────────────────────────────────────
def load_resources():
    console.print("\n[bold cyan]🔄 Loading resources...[/bold cyan]")
    model      = SentenceTransformer("all-MiniLM-L6-v2")
    client     = chromadb.PersistentClient(path="./chroma_db")
    collection = client.get_collection("investment_report")
    groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    console.print("[bold green]✅ Resources loaded![/bold green]\n")
    return model, collection, groq_client

# ── 2. Retrieve relevant context from ChromaDB ────────────────────────────
def get_context(query: str, model, collection, n_results: int = 2) -> str:
    embedding = model.encode([query]).tolist()
    results   = collection.query(query_embeddings=embedding, n_results=n_results)
    context   = "\n".join(results["documents"][0])
    return context

# ── 3. Evaluate answer + explain using Groq LLM ───────────────────────────
def evaluate_answer(question: str, options: list, correct_answer: str,
                    user_answer: str, context: str, groq_client) -> str:

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

Respond in this format:
Result: [CORRECT/INCORRECT]
Explanation: [your explanation]
Key Concept: [one key takeaway]
"""
    response = groq_client.chat.completions.create(
        model    = "llama3-8b-8192",
        messages = [{"role": "user", "content": prompt}],
        temperature = 0.3
    )
    return response.choices[0].message.content

# ── 4. Display question nicely ─────────────────────────────────────────────
def display_question(idx: int, q: dict):
    console.print(Panel(
        f"[bold yellow]Q{idx+1}: {q['question']}[/bold yellow]\n\n" +
        "\n".join([f"  [cyan]{opt}[/cyan]" for opt in q["options"]]),
        title=f"[bold]Question {idx+1} of {len(QUIZ_QUESTIONS)}[/bold]",
        border_style="blue"
    ))

# ── 5. Main Quiz Loop ──────────────────────────────────────────────────────
def run_quiz():
    console.print(Panel(
        "[bold green]💰 Welcome to the Finance Quiz Bot! 💰[/bold green]\n"
        "[white]Test your knowledge based on the 2024 Investment Report[/white]\n"
        "[dim]Answer each question with A, B, C, or D[/dim]",
        border_style="green"
    ))

    model, collection, groq_client = load_resources()

    score        = 0
    total        = len(QUIZ_QUESTIONS)
    wrong_topics = []

    for idx, q in enumerate(QUIZ_QUESTIONS):
        display_question(idx, q)

        # Get user input
        while True:
            user_input = console.input("[bold magenta]Your answer (A/B/C/D): [/bold magenta]").strip().upper()
            if user_input in ["A", "B", "C", "D"]:
                break
            console.print("[red]❌ Invalid input. Please enter A, B, C, or D[/red]")

        # Check answer
        is_correct = user_input == q["answer"]
        if is_correct:
            score += 1
            console.print("\n[bold green]✅ Correct![/bold green]")
        else:
            console.print(f"\n[bold red]❌ Incorrect! The correct answer was: {q['answer']}[/bold red]")
            wrong_topics.append(q["topic"])

        # Get context + LLM explanation
        console.print("[dim]🤖 Getting AI explanation...[/dim]")
        context     = get_context(q["question"], model, collection)
        explanation = evaluate_answer(
            q["question"], q["options"], q["answer"],
            user_input, context, groq_client
        )

        console.print(Panel(
            explanation,
            title="[bold]📚 Tutor Explanation[/bold]",
            border_style="yellow"
        ))
        console.print()

    # ── Final Score ────────────────────────────────────────────────────────
    percentage = (score / total) * 100
    if percentage >= 80:
        grade = "[bold green]🏆 Excellent![/bold green]"
    elif percentage >= 60:
        grade = "[bold yellow]👍 Good Job![/bold yellow]"
    else:
        grade = "[bold red]📖 Keep Studying![/bold red]"

    console.print(Panel(
        f"{grade}\n\n"
        f"[white]Final Score: [bold]{score}/{total}[/bold] ({percentage:.0f}%)[/white]\n\n" +
        (f"[yellow]📌 Topics to review: {', '.join(set(wrong_topics))}[/yellow]"
         if wrong_topics else "[green]✨ You got everything right![/green]"),
        title="[bold]🎯 Quiz Complete![/bold]",
        border_style="cyan"
    ))