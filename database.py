import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Optional, List, Any, Dict

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


DB_PATH = os.path.join(os.path.dirname(__file__), "contract-risk-analyzer.db")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Migration: if old schema had "name" but no "username", add username from it
    try:
        cur.execute("SELECT username FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE users ADD COLUMN username TEXT")
        cur.execute("UPDATE users SET username = name")
        conn.commit()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            risk_score INTEGER NOT NULL,
            contract_type TEXT DEFAULT 'General Contract',
            analysis_result TEXT,
            upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            summary TEXT,
            risk_factors TEXT,
            recommendations TEXT,
            clause_classification TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )
    try:
        cur.execute("SELECT analysis_result FROM contracts LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE contracts ADD COLUMN analysis_result TEXT")
        conn.commit()
    try:
        cur.execute("SELECT clause_classification FROM contracts LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE contracts ADD COLUMN clause_classification TEXT")
        conn.commit()
    try:
        cur.execute("SELECT contract_type FROM contracts LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE contracts ADD COLUMN contract_type TEXT DEFAULT 'General Contract'")
        conn.commit()
    # Keep analyses for backward compatibility; new records go to contracts
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            summary TEXT NOT NULL,
            risk_score INTEGER NOT NULL,
            risk_factors TEXT NOT NULL,
            recommendations TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )

    conn.commit()
    conn.close()


@dataclass
class User(UserMixin):
    id: int
    username: str
    email: str
    password_hash: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "User":
        return User(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            password_hash=row["password_hash"],
        )

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


def get_user_by_email(email: str) -> Optional[User]:
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if row:
        return User.from_row(row)
    return None


def get_user_by_username(username: str) -> Optional[User]:
    conn = get_db_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username.strip(),)
    ).fetchone()
    conn.close()
    if row:
        return User.from_row(row)
    return None


def create_user(username: str, email: str, password: str) -> int:
    password_hash = generate_password_hash(password)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
        (username.strip(), email.strip().lower(), password_hash),
    )
    conn.commit()
    user_id = cur.lastrowid
    conn.close()
    return user_id


def save_contract_analysis(
    user_id: int,
    filename: str,
    summary: str,
    risk_score: int,
    risk_factors: List[str],
    recommendations: List[str],
    contract_type: str = "General Contract",
    clause_classification: Optional[List[Dict[str, Any]]] = None,
    analysis_result: Optional[Dict[str, Any]] = None,
) -> int:
    conn = get_db_connection()
    cur = conn.cursor()
    risk_factors_str = "\n".join(risk_factors)
    recommendations_str = "\n".join(recommendations)
    clause_classification_json = json.dumps(clause_classification) if clause_classification else None
    analysis_result_json = json.dumps(analysis_result) if analysis_result else None
    cur.execute(
        """
        INSERT INTO contracts (
            user_id, filename, risk_score, contract_type, analysis_result, summary, risk_factors, recommendations, clause_classification
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            filename,
            risk_score,
            contract_type,
            analysis_result_json,
            summary,
            risk_factors_str,
            recommendations_str,
            clause_classification_json,
        ),
    )
    conn.commit()
    contract_id = cur.lastrowid
    conn.close()
    return contract_id


def get_user_contracts(user_id: int) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, user_id, filename, risk_score, contract_type, upload_date, analysis_result FROM contracts WHERE user_id = ? ORDER BY upload_date DESC",
        (user_id,),
    ).fetchall()
    contract_rows = []
    for row in rows:
        data = dict(row)
        if not data.get("contract_type"):
            analysis_result = data.get("analysis_result")
            if analysis_result:
                try:
                    parsed = json.loads(analysis_result)
                    data["contract_type"] = parsed.get("contract_type", "General Contract")
                except (json.JSONDecodeError, TypeError):
                    data["contract_type"] = "General Contract"
            else:
                data["contract_type"] = "General Contract"
        contract_rows.append(data)
    conn.close()
    return contract_rows


def get_user_analyses(user_id: int) -> List[Dict[str, Any]]:
    """Return contracts for dashboard; alias for get_user_contracts."""
    return get_user_contracts(user_id)

