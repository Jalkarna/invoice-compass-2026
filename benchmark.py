"""A small, fixed synthetic evaluation. This is a smoke check, not a broad model benchmark."""
import json
import re
import time
from pathlib import Path

from invoice_compass.engine import Ledger, LocalEncoder, Search, MODEL

CASES = [
    ("building an online shop", "INV-2601"),
    ("logo and brand identity", "INV-2602"),
    ("product photos for a catalog", "INV-2610"),
    ("film for a new backpack", "INV-2605"),
    ("ads on social networks", "INV-2607"),
    ("combine spreadsheets automatically", "INV-2609"),
    ("website backups and security updates", "INV-2604"),
    ("clean up a podcast recording", "INV-2611"),
]


def words(text):
    return set(re.findall(r"[a-z]+", text.lower()))


if __name__ == "__main__":
    invoices = json.loads(Path("sample_invoices.json").read_text())
    started = time.perf_counter()
    ledger = Ledger(invoices, LocalEncoder())
    startup = time.perf_counter() - started
    rows = []
    for query, expected in CASES:
        started = time.perf_counter()
        result = ledger.search(Search(query=query, status="unpaid", min_score=0, limit=12))
        elapsed = (time.perf_counter() - started) * 1000
        candidates = [i for i in ledger.invoices if i.status == "unpaid"]
        lexical = sorted(candidates, key=lambda i: (-len(words(query) & words(i.search_text())), i.id))[0]
        top = result["results"][0]
        rows.append({"query": query, "expected": expected, "semantic_top1": top["invoice"]["id"],
                     "cosine_similarity": top["similarity"], "query_ms": round(elapsed, 2),
                     "semantic_correct": top["invoice"]["id"] == expected,
                     "keyword_overlap_top1": lexical.id, "keyword_overlap_correct": lexical.id == expected})
    report = {"model": MODEL, "dataset": "12 fictional invoices; 8 fixed paraphrases; unpaid filter",
              "scope": "Handwritten synthetic smoke test, not a representative production benchmark.",
              "startup_seconds": round(startup, 2), "cases": rows,
              "semantic_top1_correct": sum(r["semantic_correct"] for r in rows),
              "keyword_overlap_top1_correct": sum(r["keyword_overlap_correct"] for r in rows),
              "total": len(rows)}
    Path("benchmark-results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
