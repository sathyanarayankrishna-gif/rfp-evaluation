# 📊 Agentic RFP Evaluation & Supplier Ranking

AI-assisted application that reads supplier proposals (PDFs), scores them against
configurable criteria, benchmarks suppliers against peers, and produces an
explainable final leaderboard.

## Architecture

```
STREAMLIT UI (app.py)
       │
ORCHESTRATOR AGENT (agent.py)
  ├── DocumentTool      → PyMuPDF PDF extraction
  ├── EvaluationAgent   → LLM criterion-wise scoring
  ├── ValidationTool    → Schema checks, normalization
  └── RankingTool       → Deterministic scoring, PPI, tie-breaks
       │
SQLite DATABASE (database.py)
  ├── evaluation_criteria
  ├── rfp_runs
  └── supplier_results
```

## Formulas

| Metric | Formula |
|--------|---------|
| Absolute Weighted Score | Sum of (score/max_score) x weight |
| Criterion Benchmark | Highest score across all suppliers per criterion |
| Criterion Gap | Supplier score - benchmark (0 for leader) |
| Relative Performance % | (score / benchmark) x 100 |
| Peer Performance Index | Weighted average of relative % |

## Tie-Break Rules
1. Higher PPI first
2. Earlier submission date
3. Higher experience rating
4. Supplier name ascending

## Setup
```bash
pip install -r requirements.txt
streamlit run app.py
```

## LLM
Uses Google Gemini (free). Get key at: https://aistudio.google.com/app/apikey
