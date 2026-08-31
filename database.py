"""
database.py — SQLite persistence layer for the RFP Evaluation System.
"""

import sqlite3
import os
import json
from datetime import datetime

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database")
DB_PATH = os.path.join(DB_DIR, "rfp_evaluation.db")


def get_connection():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_tables():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS evaluation_criteria (
            criterion_id INTEGER PRIMARY KEY,
            name         TEXT    NOT NULL,
            description  TEXT    NOT NULL,
            weight       REAL   NOT NULL,
            max_score    REAL   NOT NULL,
            is_active    INTEGER NOT NULL DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rfp_runs (
            rfp_run_id TEXT    PRIMARY KEY,
            created_at TEXT    NOT NULL,
            status     TEXT    NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS supplier_results (
            result_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            rfp_run_id      TEXT    NOT NULL,
            supplier_name   TEXT    NOT NULL,
            submission_date TEXT,
            experience_rating REAL,
            absolute_score  REAL,
            ppi             REAL,
            final_rank      INTEGER,
            result_json     TEXT,
            FOREIGN KEY (rfp_run_id) REFERENCES rfp_runs(rfp_run_id)
        )
    """)

    conn.commit()
    conn.close()


def seed_criteria():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM evaluation_criteria")
    if cur.fetchone()[0] > 0:
        conn.close()
        return

    criteria = [
        (1, "Technical Capability",
         "Architecture, integrations, scalability, technical fit",
         30.0, 10, 1),
        (2, "Implementation Plan",
         "Timeline, milestones, staffing, risk plan",
         20.0, 10, 1),
        (3, "Commercial Value",
         "Pricing clarity, total cost, assumptions",
         20.0, 10, 1),
        (4, "Security & Compliance",
         "Controls, certifications, privacy, auditability",
         20.0, 10, 1),
        (5, "Support & Experience",
         "Support model, similar projects, references",
         10.0, 10, 1),
    ]

    cur.executemany(
        """INSERT INTO evaluation_criteria
           (criterion_id, name, description, weight, max_score, is_active)
           VALUES (?, ?, ?, ?, ?, ?)""",
        criteria,
    )
    conn.commit()
    conn.close()


def get_active_criteria():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM evaluation_criteria WHERE is_active = 1 ORDER BY criterion_id"
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def create_rfp_run(rfp_run_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO rfp_runs (rfp_run_id, created_at, status) VALUES (?, ?, ?)",
        (rfp_run_id, datetime.utcnow().isoformat(), "IN_PROGRESS"),
    )
    conn.commit()
    conn.close()


def update_rfp_run_status(rfp_run_id, status):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE rfp_runs SET status = ? WHERE rfp_run_id = ?",
        (status, rfp_run_id),
    )
    conn.commit()
    conn.close()


def save_supplier_result(rfp_run_id, supplier_name, submission_date,
                         experience_rating, absolute_score, ppi,
                         final_rank, result_json):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO supplier_results
           (rfp_run_id, supplier_name, submission_date, experience_rating,
            absolute_score, ppi, final_rank, result_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (rfp_run_id, supplier_name, submission_date, experience_rating,
         absolute_score, ppi, final_rank,
         json.dumps(result_json) if isinstance(result_json, dict) else result_json),
    )
    conn.commit()
    conn.close()


def get_run_results(rfp_run_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM supplier_results WHERE rfp_run_id = ? ORDER BY final_rank",
        (rfp_run_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_all_runs():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rfp_runs ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# Bootstrap on import
create_tables()
seed_criteria()
