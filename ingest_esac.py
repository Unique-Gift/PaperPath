"""
ESAC Registry Ingestion Script
Downloads the ESAC registry Excel file and ingests all 1,500+ institutional
agreements into the PaperPath database.

Run manually:   python ingest_esac.py
Weekly sync:    set up as a cron job or Railway cron
"""

import httpx
import openpyxl
import sqlite3
import os
import time
from datetime import datetime
from io import BytesIO

ESAC_DOWNLOAD_URL = "https://keeper.mpdl.mpg.de/f/a943bbc34bd54502b0d6/?dl=1"
DB_PATH = os.getenv("DB_PATH", "paperpath.db")

def download_registry() -> bytes:
    print("📥 Downloading ESAC registry...")
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        response = client.get(ESAC_DOWNLOAD_URL)
        response.raise_for_status()
    print(f"✅ Downloaded {len(response.content) / 1024:.1f} KB")
    return response.content

def parse_date(val) -> str | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, str):
        return val[:10]
    return None

def ingest(data: bytes):
    wb = openpyxl.load_workbook(BytesIO(data), read_only=True)
    ws = wb.active

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Create ESAC table if it doesn't exist
    cur.execute("""
        CREATE TABLE IF NOT EXISTS esac_agreements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agreement_id TEXT UNIQUE,
            publisher TEXT,
            institution TEXT,
            country TEXT,
            start_date TEXT,
            end_date TEXT,
            agreement_url TEXT,
            page_url TEXT,
            is_active INTEGER DEFAULT 1,
            ingested_at TEXT
        )
    """)

    # Create index for fast institution lookup
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_esac_institution
        ON esac_agreements(institution)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_esac_publisher
        ON esac_agreements(publisher)
    """)

    inserted = 0
    updated = 0
    skipped = 0
    now = datetime.utcnow().isoformat()

    for row in ws.iter_rows(min_row=2, values_only=True):
        publisher     = row[0]
        agreement_id  = row[1]
        institution   = row[7]
        country       = row[8]
        start_date    = parse_date(row[5])
        end_date      = parse_date(row[6])
        agreement_url = row[4]
        page_url      = row[44] if len(row) > 44 else None

        if not agreement_id or not publisher:
            skipped += 1
            continue

        # Determine if active (end date in future or no end date)
        is_active = 1
        if end_date:
            try:
                if end_date < datetime.utcnow().strftime("%Y-%m-%d"):
                    is_active = 0
            except Exception:
                pass

        cur.execute("""
            INSERT INTO esac_agreements
                (agreement_id, publisher, institution, country, start_date, end_date,
                 agreement_url, page_url, is_active, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agreement_id) DO UPDATE SET
                publisher=excluded.publisher,
                institution=excluded.institution,
                country=excluded.country,
                start_date=excluded.start_date,
                end_date=excluded.end_date,
                agreement_url=excluded.agreement_url,
                page_url=excluded.page_url,
                is_active=excluded.is_active,
                ingested_at=excluded.ingested_at
        """, (agreement_id, publisher, institution, country, start_date, end_date,
              agreement_url, page_url, is_active, now))

        if cur.rowcount == 1:
            inserted += 1
        else:
            updated += 1

    conn.commit()

    # Print summary
    cur.execute("SELECT COUNT(*) FROM esac_agreements")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM esac_agreements WHERE is_active = 1")
    active = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT publisher) FROM esac_agreements")
    publishers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT institution) FROM esac_agreements")
    institutions = cur.fetchone()[0]

    conn.close()

    print(f"\n📊 Ingestion complete:")
    print(f"   Inserted:     {inserted}")
    print(f"   Updated:      {updated}")
    print(f"   Skipped:      {skipped}")
    print(f"   Total in DB:  {total}")
    print(f"   Active:       {active}")
    print(f"   Publishers:   {publishers}")
    print(f"   Institutions: {institutions}")

def get_esac_access(institution_name: str, publisher: str = None) -> list:
    """
    Query function for main.py — given an institution name or domain keyword,
    returns matching active ESAC agreements.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if publisher:
        cur.execute("""
            SELECT publisher, institution, country, start_date, end_date, agreement_url
            FROM esac_agreements
            WHERE is_active = 1
            AND institution LIKE ?
            AND publisher LIKE ?
        """, (f"%{institution_name}%", f"%{publisher}%"))
    else:
        cur.execute("""
            SELECT publisher, institution, country, start_date, end_date, agreement_url
            FROM esac_agreements
            WHERE is_active = 1
            AND institution LIKE ?
        """, (f"%{institution_name}%",))

    rows = cur.fetchall()
    conn.close()
    return rows

if __name__ == "__main__":
    start = time.time()
    data = download_registry()
    ingest(data)
    elapsed = time.time() - start
    print(f"\n⏱️  Total time: {elapsed:.1f}s")
