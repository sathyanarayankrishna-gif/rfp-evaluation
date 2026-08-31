"""
agent.py — Agentic RFP Evaluation Pipeline

Components:
  1. DocumentTool      – extracts text from PDF
  2. EvaluationAgent   – LLM scores one supplier against criteria
  3. ValidationTool    – validates/normalises LLM JSON
  4. RankingTool       – deterministic scoring, benchmarking, PPI, tie-breaks
  5. OrchestratorAgent – wires everything together
"""

import json
import re
import uuid
from datetime import datetime

import fitz  # PyMuPDF

from database import (
    get_active_criteria,
    create_rfp_run,
    update_rfp_run_status,
    save_supplier_result,
)


# ═══════════════════════════════════════════════════════════════════════
# 1. DOCUMENT TOOL
# ═══════════════════════════════════════════════════════════════════════

class DocumentTool:
    @staticmethod
    def extract_text(pdf_path):
        doc = fitz.open(pdf_path)
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        text = "\n".join(pages)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()


# ═══════════════════════════════════════════════════════════════════════
# 2. EVALUATION AGENT (LLM-powered)
# ═══════════════════════════════════════════════════════════════════════

class EvaluationAgent:
    def __init__(self, provider="google", api_key=""):
        self.provider = provider.lower()
        self.api_key = api_key
        self._setup_client()

    def _setup_client(self):
        if self.provider == "google":
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel("gemini-2.5-flash")
        elif self.provider == "openai":
            from openai import OpenAI
            self.client = OpenAI(api_key=self.api_key)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    def _build_prompt(self, supplier_name, document_text, criteria):
        criteria_block = "\n".join(
            f"  - criterion_id: {c['criterion_id']}, "
            f"name: \"{c['name']}\", "
            f"description: \"{c['description']}\", "
            f"max_score: {c['max_score']}, "
            f"weight: {c['weight']}%"
            for c in criteria
        )

        prompt = f"""You are an expert procurement evaluator.

TASK:
Evaluate the following supplier RFP response against the criteria listed below.
For EACH criterion, provide a score, justification, and supporting evidence
quoted from the document.

RULES:
1. Use ONLY evidence present in the supplier document below.
2. Return EXACTLY one result for EVERY criterion listed.
3. Each score must be an integer between 0 and the criterion's max_score.
4. Output valid JSON ONLY — no markdown, no commentary, no code fences.

CRITERIA:
{criteria_block}

SUPPLIER NAME: {supplier_name}

SUPPLIER DOCUMENT:
\"\"\"{document_text[:12000]}\"\"\"

OUTPUT FORMAT (strict JSON):
{{
  "supplier_name": "{supplier_name}",
  "criteria": [
    {{
      "criterion_id": <int>,
      "criterion_name": "<name>",
      "score": <int 0..max_score>,
      "max_score": <int>,
      "justification": "<why this score>",
      "evidence": "<direct quote or reference from the document>"
    }}
  ],
  "risks": ["<risk 1>", "<risk 2>"],
  "overall_summary": "<2-3 sentence summary>"
}}
"""
        return prompt

    def evaluate(self, supplier_name, document_text, criteria):
        prompt = self._build_prompt(supplier_name, document_text, criteria)

        try:
            if self.provider == "google":
                response = self.model.generate_content(prompt)
                raw = response.text
            elif self.provider == "openai":
                response = self.client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                )
                raw = response.choices[0].message.content
            else:
                return {"error": f"Unknown provider {self.provider}"}

            raw = raw.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)

            return json.loads(raw)

        except json.JSONDecodeError as e:
            return {"error": f"JSON parse error: {str(e)}", "raw_response": raw}
        except Exception as e:
            return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════
# 3. VALIDATION TOOL
# ═══════════════════════════════════════════════════════════════════════

class ValidationTool:
    @staticmethod
    def validate(llm_output, criteria, supplier_name):
        warnings = []

        if "error" in llm_output:
            warnings.append(f"LLM error: {llm_output['error']}")
            fallback = []
            for c in criteria:
                fallback.append({
                    "criterion_id": c["criterion_id"],
                    "criterion_name": c["name"],
                    "score": 0,
                    "max_score": c["max_score"],
                    "justification": "LLM failed — score defaulted to 0.",
                    "evidence": "N/A",
                })
            return {
                "supplier_name": supplier_name,
                "criteria": fallback,
                "risks": [],
                "overall_summary": "Evaluation could not be completed.",
                "warnings": warnings,
            }

        llm_criteria = {}
        for item in llm_output.get("criteria", []):
            cid = item.get("criterion_id")
            if cid is not None:
                llm_criteria[int(cid)] = item

        validated = []
        for c in criteria:
            cid = c["criterion_id"]
            max_score = c["max_score"]

            if cid in llm_criteria:
                entry = llm_criteria[cid]
                score = entry.get("score", 0)

                if not isinstance(score, (int, float)):
                    warnings.append(f"Criterion {cid}: non-numeric score -> 0")
                    score = 0
                if score < 0:
                    warnings.append(f"Criterion {cid}: negative score {score} -> 0")
                    score = 0
                if score > max_score:
                    warnings.append(f"Criterion {cid}: score {score} > max {max_score} -> clipped")
                    score = max_score

                validated.append({
                    "criterion_id": cid,
                    "criterion_name": c["name"],
                    "score": round(float(score), 2),
                    "max_score": max_score,
                    "justification": entry.get("justification", "No justification."),
                    "evidence": entry.get("evidence", "No evidence cited."),
                })
            else:
                warnings.append(f"Criterion {cid} ('{c['name']}') missing -> score 0")
                validated.append({
                    "criterion_id": cid,
                    "criterion_name": c["name"],
                    "score": 0,
                    "max_score": max_score,
                    "justification": "Not returned by LLM — defaulted to 0.",
                    "evidence": "N/A",
                })

        return {
            "supplier_name": llm_output.get("supplier_name", supplier_name),
            "criteria": validated,
            "risks": llm_output.get("risks", []),
            "overall_summary": llm_output.get("overall_summary", ""),
            "warnings": warnings,
        }


# ═══════════════════════════════════════════════════════════════════════
# 4. RANKING TOOL (deterministic — NO LLM)
# ═══════════════════════════════════════════════════════════════════════

class RankingTool:
    @staticmethod
    def calculate_absolute_score(validated, criteria):
        total = 0.0
        for vc in validated["criteria"]:
            cid = vc["criterion_id"]
            weight = next(
                (c["weight"] for c in criteria if c["criterion_id"] == cid), 0
            )
            max_s = vc["max_score"]
            if max_s > 0:
                total += (vc["score"] / max_s) * weight
        return round(total, 4)

    @staticmethod
    def compute_benchmarks(all_validated, criteria):
        benchmarks = {}
        for c in criteria:
            cid = c["criterion_id"]
            best = 0
            for v in all_validated:
                for vc in v["criteria"]:
                    if vc["criterion_id"] == cid:
                        if vc["score"] > best:
                            best = vc["score"]
            benchmarks[cid] = best
        return benchmarks

    @staticmethod
    def enrich_with_peer_metrics(validated, benchmarks, criteria):
        weighted_rel_sum = 0.0
        total_weight = 0.0

        for vc in validated["criteria"]:
            cid = vc["criterion_id"]
            bench = benchmarks.get(cid, 0)
            vc["benchmark"] = bench
            vc["gap"] = round(vc["score"] - bench, 2)

            if bench > 0:
                rel_pct = round((vc["score"] / bench) * 100, 2)
            else:
                rel_pct = 100.0

            vc["relative_pct"] = rel_pct

            weight = next(
                (c["weight"] for c in criteria if c["criterion_id"] == cid), 0
            )
            vc["weight"] = weight
            weighted_rel_sum += rel_pct * weight
            total_weight += weight

        ppi = round(weighted_rel_sum / total_weight, 4) if total_weight > 0 else 0.0
        validated["ppi"] = ppi
        return validated

    @staticmethod
    def rank_suppliers(suppliers_data):
        def sort_key(s):
            return (
                -s.get("ppi", 0),
                s.get("submission_date", "9999-12-31"),
                -s.get("experience_rating", 0),
                s.get("supplier_name", ""),
            )

        ranked = sorted(suppliers_data, key=sort_key)
        for idx, s in enumerate(ranked, start=1):
            s["final_rank"] = idx
        return ranked


# ═══════════════════════════════════════════════════════════════════════
# 5. ORCHESTRATOR AGENT
# ═══════════════════════════════════════════════════════════════════════

class OrchestratorAgent:
    def __init__(self, llm_provider="google", api_key=""):
        self.doc_tool = DocumentTool()
        self.eval_agent = EvaluationAgent(provider=llm_provider, api_key=api_key)
        self.val_tool = ValidationTool()
        self.rank_tool = RankingTool()

    def run_batch(self, suppliers, progress_callback=None):
        def log(msg):
            if progress_callback:
                progress_callback(msg)

        log("Step 1/9 — Loading active criteria from database...")
        criteria = get_active_criteria()
        if not criteria:
            return {"error": "No active evaluation criteria found."}

        rfp_run_id = f"RUN-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        log(f"Step 2/9 — Created batch: {rfp_run_id}")
        create_rfp_run(rfp_run_id)

        all_validated = []
        all_warnings = []

        for i, supplier in enumerate(suppliers, start=1):
            name = supplier["supplier_name"]
            log(f"Step 3/9 — [{i}/{len(suppliers)}] Extracting PDF for {name}...")
            doc_text = self.doc_tool.extract_text(supplier["pdf_path"])

            log(f"Step 4/9 — [{i}/{len(suppliers)}] LLM evaluating {name}...")
            llm_output = self.eval_agent.evaluate(name, doc_text, criteria)

            log(f"Step 5/9 — [{i}/{len(suppliers)}] Validating {name}...")
            validated = self.val_tool.validate(llm_output, criteria, name)
            validated["supplier_name"] = name
            validated["submission_date"] = supplier.get("submission_date", "")
            validated["experience_rating"] = supplier.get("experience_rating", 5.0)

            if validated["warnings"]:
                all_warnings.extend([f"{name}: {w}" for w in validated["warnings"]])

            all_validated.append(validated)

        log("Step 6/9 — Calculating absolute weighted scores...")
        for v in all_validated:
            v["absolute_score"] = self.rank_tool.calculate_absolute_score(v, criteria)

        log("Step 7/9 — Computing peer benchmarks...")
        benchmarks = self.rank_tool.compute_benchmarks(all_validated, criteria)
        for v in all_validated:
            self.rank_tool.enrich_with_peer_metrics(v, benchmarks, criteria)

        log("Step 8/9 — Ranking suppliers with tie-break rules...")
        ranked = self.rank_tool.rank_suppliers(all_validated)

        log("Step 9/9 — Saving results to database...")
        for r in ranked:
            save_supplier_result(
                rfp_run_id=rfp_run_id,
                supplier_name=r["supplier_name"],
                submission_date=r.get("submission_date", ""),
                experience_rating=r.get("experience_rating", 0),
                absolute_score=r.get("absolute_score", 0),
                ppi=r.get("ppi", 0),
                final_rank=r.get("final_rank", 0),
                result_json=r,
            )

        update_rfp_run_status(rfp_run_id, "COMPLETED")
        log("✅ Batch complete!")

        return {
            "rfp_run_id": rfp_run_id,
            "created_at": datetime.utcnow().isoformat(),
            "status": "COMPLETED",
            "criteria_used": criteria,
            "benchmarks": {str(k): v for k, v in benchmarks.items()},
            "suppliers": ranked,
            "warnings": all_warnings,
            "tie_break_rules": [
                "1) Higher PPI first",
                "2) Earlier submission date",
                "3) Higher historical experience rating",
                "4) Supplier name ascending (alphabetical)",
            ],
        }
