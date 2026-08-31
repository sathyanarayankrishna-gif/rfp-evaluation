"""
app.py — Streamlit UI for the Agentic RFP Evaluation System

Screens:
  1. Criteria         — active criteria, weights, max scores
  2. Supplier Input   — PDF upload, metadata, validation, Evaluate button
  3. Leaderboard      — rank, supplier, absolute score, PPI, date, rating
  4. Detailed Scorecard — criterion score, benchmark, gap, relative %, evidence
  5. Run Details      — RFP_RUN_ID, warnings, tie-break explanation, JSON download
"""

import streamlit as st
import json
import os
import tempfile
from datetime import date

from database import get_active_criteria, get_all_runs, get_run_results
from agent import OrchestratorAgent

# ─────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="RFP Evaluation System",
    page_icon="📊",
    layout="wide",
)

st.title("📊 Agentic RFP Evaluation & Supplier Ranking")
st.caption("AI-assisted proposal scoring with deterministic ranking and full explainability")

# ─────────────────────────────────────────────────────────────────────────
# Sidebar — API key
# ─────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")

    llm_provider = st.selectbox(
        "LLM Provider",
        ["google", "openai"],
        help="Google Gemini (free) or OpenAI",
    )

    api_key = st.text_input(
        "API Key",
        type="password",
        help="Get a free Gemini key at https://aistudio.google.com/app/apikey",
    )

    # Try to load from Streamlit secrets or environment
    if not api_key:
        try:
            api_key = st.secrets.get("GOOGLE_API_KEY", "") or st.secrets.get("OPENAI_API_KEY", "")
        except Exception:
            api_key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")

    st.divider()
    st.markdown("**Tie-Break Rules:**")
    st.markdown("""
    1. Higher PPI first
    2. Earlier submission date
    3. Higher experience rating
    4. Supplier name (A→Z)
    """)

# ─────────────────────────────────────────────────────────────────────────
# Tabs
# ─────────────────────────────────────────────────────────────────────────
tab_criteria, tab_input, tab_results, tab_history = st.tabs([
    "📋 Criteria", "📤 Supplier Input", "🏆 Results", "📁 Past Runs"
])

# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — CRITERIA
# ═══════════════════════════════════════════════════════════════════════════
with tab_criteria:
    st.subheader("Active Evaluation Criteria")
    criteria = get_active_criteria()

    if criteria:
        import pandas as pd
        df = pd.DataFrame(criteria)
        df = df[["criterion_id", "name", "description", "weight", "max_score"]]
        df.columns = ["ID", "Criterion", "Description", "Weight (%)", "Max Score"]
        st.dataframe(df, use_container_width=True, hide_index=True)

        total_weight = sum(c["weight"] for c in criteria)
        if abs(total_weight - 100.0) < 0.01:
            st.success(f"Total weight: {total_weight}% ✓")
        else:
            st.warning(f"Total weight: {total_weight}% — should be 100%")
    else:
        st.error("No active criteria found. Check database setup.")

# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — SUPPLIER INPUT
# ═══════════════════════════════════════════════════════════════════════════
with tab_input:
    st.subheader("Upload Supplier RFP Documents")

    # File uploader
    uploaded_files = st.file_uploader(
        "Upload RFP PDF files",
        type=["pdf"],
        accept_multiple_files=True,
        help="Upload one PDF per supplier",
    )

    if uploaded_files:
        st.write(f"**{len(uploaded_files)} file(s) uploaded**")

        # Metadata entry for each file
        suppliers_meta = []
        valid = True

        for i, uf in enumerate(uploaded_files):
            with st.expander(f"📄 {uf.name}", expanded=(i == 0)):
                col1, col2, col3 = st.columns(3)

                with col1:
                    name = st.text_input(
                        "Supplier Name",
                        value=uf.name.replace(".pdf", "").replace("_", " ").replace("-", " ").title(),
                        key=f"name_{i}",
                    )

                with col2:
                    sub_date = st.date_input(
                        "Submission Date",
                        value=date(2024, 11, 15),
                        key=f"date_{i}",
                    )

                with col3:
                    exp_rating = st.slider(
                        "Experience Rating",
                        min_value=1.0,
                        max_value=10.0,
                        value=7.0,
                        step=0.5,
                        key=f"exp_{i}",
                    )

                if not name.strip():
                    st.error("Supplier name is required.")
                    valid = False

                suppliers_meta.append({
                    "supplier_name": name.strip(),
                    "submission_date": sub_date.isoformat(),
                    "experience_rating": exp_rating,
                    "file_obj": uf,
                })

        st.divider()

        # Validate
        if not api_key:
            st.warning("Please enter your API key in the sidebar before evaluating.")

        # Evaluate button
        if st.button("🚀 Evaluate All Suppliers", type="primary",
                      disabled=(not valid or not api_key)):
            # Save uploaded PDFs to temp files
            suppliers_for_agent = []
            temp_dir = tempfile.mkdtemp()

            for sm in suppliers_meta:
                temp_path = os.path.join(temp_dir, sm["file_obj"].name)
                with open(temp_path, "wb") as f:
                    f.write(sm["file_obj"].getbuffer())
                suppliers_for_agent.append({
                    "supplier_name": sm["supplier_name"],
                    "pdf_path": temp_path,
                    "submission_date": sm["submission_date"],
                    "experience_rating": sm["experience_rating"],
                })

            # Run the orchestrator
            status_area = st.empty()
            progress_bar = st.progress(0)

            step_count = [0]
            total_steps = 5 + len(suppliers_for_agent) * 3  # approximate

            def progress_cb(msg):
                step_count[0] += 1
                progress_bar.progress(
                    min(step_count[0] / total_steps, 1.0)
                )
                status_area.info(msg)

            try:
                orchestrator = OrchestratorAgent(
                    llm_provider=llm_provider,
                    api_key=api_key,
                )
                result = orchestrator.run_batch(
                    suppliers_for_agent,
                    progress_callback=progress_cb,
                )

                progress_bar.progress(1.0)
                status_area.success("✅ Evaluation complete! Switch to the Results tab.")

                st.session_state["last_result"] = result

            except Exception as e:
                st.error(f"Evaluation failed: {str(e)}")

    else:
        st.info("Upload one or more supplier RFP PDF files to begin.")

# ═══════════════════════════════════════════════════════════════════════════
# TAB 3 — RESULTS (Leaderboard + Scorecard)
# ═══════════════════════════════════════════════════════════════════════════
with tab_results:
    result = st.session_state.get("last_result")

    if result and "error" not in result:
        st.subheader(f"Run: {result['rfp_run_id']}")

        # ── Leaderboard ────────────────────────────────────────────────
        st.markdown("### 🏆 Leaderboard")

        import pandas as pd
        leaderboard_data = []
        for s in result["suppliers"]:
            leaderboard_data.append({
                "Rank": s["final_rank"],
                "Supplier": s["supplier_name"],
                "Absolute Score": round(s["absolute_score"], 2),
                "PPI": round(s["ppi"], 2),
                "Submission Date": s.get("submission_date", ""),
                "Experience Rating": s.get("experience_rating", ""),
            })
        df_lb = pd.DataFrame(leaderboard_data)
        st.dataframe(df_lb, use_container_width=True, hide_index=True)

        # ── Detailed Scorecards ────────────────────────────────────────
        st.markdown("### 📊 Detailed Scorecards")

        for s in result["suppliers"]:
            with st.expander(
                f"{'🥇' if s['final_rank'] == 1 else '📄'} "
                f"Rank #{s['final_rank']} — {s['supplier_name']} "
                f"(Score: {s['absolute_score']:.2f}, PPI: {s['ppi']:.2f})"
            ):
                scorecard_data = []
                for c in s["criteria"]:
                    scorecard_data.append({
                        "Criterion": c.get("criterion_name", f"ID {c['criterion_id']}"),
                        "Score": c["score"],
                        "Max": c["max_score"],
                        "Benchmark": c.get("benchmark", "—"),
                        "Gap": c.get("gap", "—"),
                        "Relative %": c.get("relative_pct", "—"),
                        "Weight %": c.get("weight", "—"),
                    })
                st.dataframe(
                    pd.DataFrame(scorecard_data),
                    use_container_width=True,
                    hide_index=True,
                )

                # Evidence & Justification
                for c in s["criteria"]:
                    cname = c.get("criterion_name", f"Criterion {c['criterion_id']}")
                    st.markdown(f"**{cname}** — Score: {c['score']}/{c['max_score']}")
                    st.markdown(f"*Justification:* {c.get('justification', 'N/A')}")
                    st.markdown(f"*Evidence:* {c.get('evidence', 'N/A')}")
                    st.markdown("---")

                # Risks
                if s.get("risks"):
                    st.markdown("**Identified Risks:**")
                    for risk in s["risks"]:
                        st.markdown(f"- {risk}")

                # Summary
                if s.get("overall_summary"):
                    st.markdown(f"**Summary:** {s['overall_summary']}")

        # ── Run Details ────────────────────────────────────────────────
        st.markdown("### 📋 Run Details")

        col1, col2 = st.columns(2)
        with col1:
            st.metric("RFP Run ID", result["rfp_run_id"])
            st.metric("Status", result["status"])

        with col2:
            st.metric("Suppliers Evaluated", len(result["suppliers"]))
            st.metric("Created", result["created_at"][:19])

        # Tie-break explanation
        st.markdown("**Tie-Break Rules Applied:**")
        for rule in result.get("tie_break_rules", []):
            st.markdown(f"- {rule}")

        # Warnings
        if result.get("warnings"):
            st.markdown("**⚠️ Warnings:**")
            for w in result["warnings"]:
                st.warning(w)

        # JSON Download
        st.markdown("### 💾 Export")
        json_str = json.dumps(result, indent=2, default=str)
        st.download_button(
            label="📥 Download Full Result as JSON",
            data=json_str,
            file_name=f"{result['rfp_run_id']}.json",
            mime="application/json",
        )
    else:
        st.info("No results yet. Upload supplier PDFs and run an evaluation in the Supplier Input tab.")

# ═══════════════════════════════════════════════════════════════════════════
# TAB 4 — PAST RUNS
# ═══════════════════════════════════════════════════════════════════════════
with tab_history:
    st.subheader("Past Evaluation Runs")
    runs = get_all_runs()

    if runs:
        import pandas as pd
        for run in runs:
            with st.expander(f"🔹 {run['rfp_run_id']} — {run['status']} — {run['created_at'][:19]}"):
                results = get_run_results(run["rfp_run_id"])
                if results:
                    hist_data = []
                    for r in results:
                        hist_data.append({
                            "Rank": r["final_rank"],
                            "Supplier": r["supplier_name"],
                            "Absolute Score": round(r["absolute_score"], 2) if r["absolute_score"] else 0,
                            "PPI": round(r["ppi"], 2) if r["ppi"] else 0,
                        })
                    st.dataframe(pd.DataFrame(hist_data), use_container_width=True, hide_index=True)

                    # Allow downloading individual run results
                    full_results = []
                    for r in results:
                        try:
                            rj = json.loads(r["result_json"]) if isinstance(r["result_json"], str) else r["result_json"]
                        except Exception:
                            rj = {}
                        full_results.append(rj)

                    st.download_button(
                        label="📥 Download this run as JSON",
                        data=json.dumps(full_results, indent=2, default=str),
                        file_name=f"{run['rfp_run_id']}.json",
                        mime="application/json",
                        key=f"dl_{run['rfp_run_id']}",
                    )
                else:
                    st.write("No supplier results found for this run.")
    else:
        st.info("No past runs found.")
