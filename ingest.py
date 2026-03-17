import os
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
import chromadb
from chromadb.config import Settings

load_dotenv()

# ── 1. Load the investment report ──────────────────────────────────────────
def load_report(filepath: str) -> str:
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()

# ── 2. Split report into chunks ────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = 300, overlap: int = 50) -> list:
    words = text.split()
    chunks = []
    start  = 0
    while start < len(words):
        end   = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start = end - overlap          # overlap keeps context between chunks
    return chunks

# ── 3. Embed & store in ChromaDB ───────────────────────────────────────────
def ingest_report(filepath: str = "data/investment_report.txt"):
    print("📄 Loading investment report...")
    text   = load_report(filepath)

    print("✂️  Chunking text...")
    chunks = chunk_text(text)
    print(f"   → {len(chunks)} chunks created")

    print("🤖 Loading HuggingFace embedding model (first run downloads it)...")
    model  = SentenceTransformer("all-MiniLM-L6-v2")   # free, ~80 MB

    print("🔢 Generating embeddings...")
    embeddings = model.encode(chunks).tolist()

    print("🗄️  Storing in ChromaDB...")
    client     = chromadb.PersistentClient(path="./chroma_db")

    # Delete old collection if it exists (clean re-ingest)
    existing = [c.name for c in client.list_collections()]
    if "investment_report" in existing:
        client.delete_collection("investment_report")

    collection = client.get_or_create_collection(
        name     = "investment_report",
        metadata = {"hnsw:space": "cosine"}
    )

    collection.add(
        documents  = chunks,
        embeddings = embeddings,
        ids        = [f"chunk_{i}" for i in range(len(chunks))]
    )

    print(f"✅ Successfully ingested {len(chunks)} chunks into ChromaDB!")
    print("📁 Vector DB saved at: ./chroma_db")

# ── 4. Run ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ingest_report()