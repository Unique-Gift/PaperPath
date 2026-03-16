import sqlite3
import json
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "paperpath.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def get_connection():
    """Returns a SQLite connection with row factory for dict-like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row 
    return conn


def init_db():
    """
    Runs schema.sql to create all tables if they don't exist.
    Safe to call on every startup — won't overwrite existing data.
    """
    if not os.path.exists(SCHEMA_PATH):
        raise FileNotFoundError(f"schema.sql not found at {SCHEMA_PATH}")

    with open(SCHEMA_PATH, "r") as f:
        schema = f.read()

    conn = get_connection()
    try:
        conn.executescript(schema)
        conn.commit()
        print("✅ Database initialized successfully")
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        raise
    finally:
        conn.close()

def get_cached_paper(doi: str) -> Optional[dict]:
    """
    Returns cached paper result if it exists and hasn't expired.
    Returns None if not found or expired.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM papers
            WHERE doi = ?
            AND cache_expires_at > datetime('now')
            """,
            (doi.lower().strip(),)
        ).fetchone()

        if row is None:
            return None

        # Fetch associated free sources
        sources = conn.execute(
            """
            SELECT * FROM free_access_sources
            WHERE paper_id = ?
            ORDER BY
                CASE version_type
                    WHEN 'published'       THEN 1
                    WHEN 'author_accepted' THEN 2
                    WHEN 'preprint'        THEN 3
                    WHEN 'submitted'       THEN 4
                    ELSE 5
                END
            """,
            (row["id"],)
        ).fetchall()

        author = conn.execute(
            """
            SELECT a.name, a.email, a.orcid
            FROM authors a
            JOIN paper_authors pa ON pa.author_id = a.id
            WHERE pa.paper_id = ?
            AND pa.is_corresponding = 1
            LIMIT 1
            """,
            (row["id"],)
        ).fetchone()

        return {
            "doi": row["doi"],
            "title": row["title"],
            "is_open_access": bool(row["is_open_access"]),
            "oa_status": row["oa_status"],
            "free_sources": [
                {
                    "source": s["source_name"],
                    "url": s["source_url"],
                    "version": s["version_type"],
                    "legal": bool(s["is_legal"])
                }
                for s in sources
            ],
            "author_contact": {
                "name": author["name"],
                "email": author["email"],
                "orcid": author["orcid"],
                "note": "Email not publicly available — contact via institution or ResearchGate"
            } if author else None,
             "cached": True
        }

    finally:
        conn.close()


def store_paper(doi: str, result: dict, oa_status: str = "closed"):
    """
    Stores a paper result in the cache.
    Cache duration depends on OA status.
    """
    expiry_map = {
        "gold":   "datetime('now', '+30 days')",
        "green":  "datetime('now', '+30 days')",
        "hybrid": "datetime('now', '+7 days')",
        "bronze": "datetime('now', '+7 days')",
        "closed": "datetime('now', '+7 days')",
    }
    expiry = expiry_map.get(oa_status, "datetime('now', '+7 days')")

    conn = get_connection()
    try:

        conn.execute(
            f"""
            INSERT INTO papers (doi, title, is_open_access, oa_status, cached_at, cache_expires_at)
            VALUES (?, ?, ?, ?, datetime('now'), {expiry})
            ON CONFLICT(doi) DO UPDATE SET
                title            = excluded.title,
                is_open_access   = excluded.is_open_access,
                oa_status        = excluded.oa_status,
                cached_at        = datetime('now'),
                cache_expires_at = {expiry}
            """,
            (
                doi.lower().strip(),
                result.get("title"),
                result.get("is_open_access", False),
                oa_status
            )
        )
        conn.commit()

        paper_id = conn.execute(
            "SELECT id FROM papers WHERE doi = ?",
            (doi.lower().strip(),)
        ).fetchone()["id"]

        if result.get("free_sources"):
            conn.execute(
                "DELETE FROM free_access_sources WHERE paper_id = ?",
                (paper_id,)
            )
            for source in result["free_sources"]:
                conn.execute(
                    """
                    INSERT INTO free_access_sources
                        (paper_id, source_name, source_url, version_type, is_legal)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        paper_id,
                        source.get("source"),
                        source.get("url"),
                        source.get("version", "preprint"),
                        source.get("legal", True)
                    )
                )

        if result.get("author_contact"):
            author = result["author_contact"]
            conn.execute(
                """
                INSERT OR IGNORE INTO authors (name, email, orcid)
                VALUES (?, ?, ?)
                """,
                (
                    author.get("name"),
                    author.get("email"),
                    author.get("orcid")
                )
            )
            author_id = conn.execute(
                "SELECT id FROM authors WHERE name = ? LIMIT 1",
                (author.get("name"),)
            ).fetchone()

            if author_id:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO paper_authors
                        (paper_id, author_id, is_corresponding)
                    VALUES (?, ?, 1)
                    """,
                    (paper_id, author_id["id"])
                )

        conn.commit()
        print(f"✅ Cached paper: {doi}")

    except Exception as e:
        print(f"❌ Failed to cache paper {doi}: {e}")
        conn.rollback()
    finally:
        conn.close()


def get_institutional_access(domain: str) -> Optional[dict]:
    """
    Given an email domain (e.g. 'mit.edu'), checks if the institution
    has a known access agreement with any publisher.
    Returns the agreement details or None.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT
                i.name        AS institution_name,
                i.short_name,
                p.name        AS publisher_name,
                aa.agreement_type,
                aa.access_end,
                aa.notes
            FROM institutions i
            JOIN access_agreements aa ON aa.institution_id = i.id
            LEFT JOIN publishers p    ON p.id = aa.publisher_id
            WHERE i.domain = ?
            AND (aa.access_end IS NULL OR aa.access_end > date('now'))
            LIMIT 1
            """,
            (domain.lower().strip(),)
        ).fetchone()

        if row is None:
            return None

        return {
            "detected_institution": row["institution_name"],
            "short_name": row["short_name"],
            "has_access": True,
            "publisher": row["publisher_name"],
            "agreement_type": row["agreement_type"],
            "expires": row["access_end"],
            "notes": row["notes"]
        }

    finally:
        conn.close()

def log_api_call(
    source: str,
    query: str,
    status_code: Optional[int],
    response_time_ms: int,
    success: bool,
    error_message: Optional[str] = None
):
    """
    Logs every upstream API call for debugging and rate limit tracking.
    """
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO api_call_logs
                (source_name, query, status_code, response_time_ms, success, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source, query, status_code, response_time_ms, success, error_message)
        )
        conn.commit()
    except Exception as e:
        print(f"⚠️ Failed to log API call: {e}")
    finally:
        conn.close()


def get_recent_failures(source: str, minutes: int = 5) -> int:
    """
    Returns number of failed API calls for a source in the last N minutes.
    Used by circuit_breaker.py to decide whether to skip a source.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) as failure_count
            FROM api_call_logs
            WHERE source_name = ?
            AND success = 0
            AND called_at > datetime('now', ? )
            """,
            (source, f"-{minutes} minutes")
        ).fetchone()

        return row["failure_count"] if row else 0

    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
    print("Database ready at:", DB_PATH)

def get_esac_access(institution_keyword: str, publisher: str = None) -> list:
    """
    Query ESAC registry for active agreements matching an institution name keyword.
    Returns list of matching agreements.
    """
    conn = get_connection()
    cur = conn.cursor()

    if publisher:
        cur.execute("""
            SELECT publisher, institution, country, start_date, end_date, agreement_url
            FROM esac_agreements
            WHERE is_active = 1
            AND institution LIKE ?
            AND publisher LIKE ?
            LIMIT 10
        """, (f"%{institution_keyword}%", f"%{publisher}%"))
    else:
        cur.execute("""
            SELECT publisher, institution, country, start_date, end_date, agreement_url
            FROM esac_agreements
            WHERE is_active = 1
            AND institution LIKE ?
            LIMIT 10
        """, (f"%{institution_keyword}%",))

    rows = cur.fetchall()
    conn.close()
    return [
        {
            "publisher": r[0],
            "institution": r[1],
            "country": r[2],
            "start_date": r[3],
            "end_date": r[4],
            "agreement_url": r[5],
            "source": "ESAC Registry"
        }
        for r in rows
    ]