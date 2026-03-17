import os
import sys
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv()
console = Console()

def check_environment():
    """Check all required files and API key exist before starting"""
    
    console.print("\n[bold cyan]🔍 Checking environment...[/bold cyan]")
    
    # Check API Key
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key == "your_groq_api_key_here":
        console.print("[bold red]❌ GROQ_API_KEY not set in .env file![/bold red]")
        console.print("[yellow]👉 Get your free key at: https://console.groq.com[/yellow]")
        sys.exit(1)
    console.print("[green]✅ Groq API Key found[/green]")

    # Check investment report exists
    if not os.path.exists("data/investment_report.txt"):
        console.print("[bold red]❌ Investment report not found at data/investment_report.txt[/bold red]")
        sys.exit(1)
    console.print("[green]✅ Investment report found[/green]")

    # Check ChromaDB exists
    if not os.path.exists("./chroma_db"):
        console.print("[bold red]❌ ChromaDB not found! Please run ingest.py first[/bold red]")
        console.print("[yellow]👉 Run: python ingest.py[/yellow]")
        sys.exit(1)
    console.print("[green]✅ ChromaDB vector store found[/green]")

    console.print("[bold green]✅ All checks passed!\n[/bold green]")


def show_welcome():
    """Display welcome banner"""
    console.print(Panel.fit(
        "[bold green]💰 FINANCE QUIZ & TUTOR BOT 💰[/bold green]\n\n"
        "[white]📊 Powered by:[/white]\n"
        "  [cyan]• Groq API (LLaMA 3)[/cyan]     → AI Evaluation & Explanations\n"
        "  [cyan]• ChromaDB[/cyan]                → Vector Knowledge Base\n"
        "  [cyan]• HuggingFace Embeddings[/cyan]  → Semantic Search\n\n"
        "[dim]Based on: 2024 Global Investment Report[/dim]",
        border_style="bold green"
    ))


def show_menu():
    """Display main menu and get user choice"""
    console.print(Panel(
        "[bold yellow]What would you like to do?[/bold yellow]\n\n"
        "  [cyan]1[/cyan] → Start Quiz\n"
        "  [cyan]2[/cyan] → Re-ingest Investment Report\n"
        "  [cyan]3[/cyan] → Exit",
        title="[bold]📋 Main Menu[/bold]",
        border_style="blue"
    ))

    while True:
        choice = console.input("[bold magenta]Enter choice (1/2/3): [/bold magenta]").strip()
        if choice in ["1", "2", "3"]:
            return choice
        console.print("[red]❌ Invalid choice. Enter 1, 2 or 3[/red]")


def main():
    # Show welcome banner
    show_welcome()

    # Check everything is set up correctly
    check_environment()

    while True:
        choice = show_menu()

        if choice == "1":
            # ── Start the Quiz ─────────────────────────────────────────
            console.print("\n[bold green]🚀 Starting Quiz...[/bold green]\n")
            from quiz_bot import run_quiz
            run_quiz()

            # After quiz, ask to play again
            again = console.input(
                "\n[bold magenta]Would you like to take the quiz again? (y/n): [/bold magenta]"
            ).strip().lower()
            if again != "y":
                break

        elif choice == "2":
            # ── Re-ingest report ───────────────────────────────────────
            console.print("\n[bold cyan]🔄 Re-ingesting investment report...[/bold cyan]\n")
            from ingest import ingest_report
            ingest_report()
            console.print("[bold green]✅ Report re-ingested successfully![/bold green]\n")

        elif choice == "3":
            break

    # Goodbye message
    console.print(Panel(
        "[bold green]👋 Thanks for using Finance Quiz Bot![/bold green]\n"
        "[white]Keep learning and investing wisely! 📈[/white]",
        border_style="green"
    ))


if __name__ == "__main__":
    main()