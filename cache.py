import time
from typing import Optional
from database import get_cached_paper, store_paper, log_api_call

# CONFIGURATION

# Cache duration in seconds based on OA status
CACHE_TTL = {
    "gold":    30 * 24 * 60 * 60,  # 30 days — fully open, stable
    "green":   30 * 24 * 60 * 60,  # 30 days — open archive, stable
    "hybrid":   7 * 24 * 60 * 60,  # 7 days  — mixed, can change
    "bronze":   7 * 24 * 60 * 60,  # 7 days  — free but not licensed
    "closed":   7 * 24 * 60 * 60,  # 7 days  — recheck regularly
    "unknown":  1 * 24 * 60 * 60,  # 1 day   — recheck soon
}

# CACHE CHECK

def get_from_cache(doi: str) -> Optional[dict]:
    """
    Checks SQLite for a cached result for this DOI.
    Returns the cached result if found and not expired.
    Returns None if not found or expired — caller should hit APIs.
    """
    start = time.time()

    try:
        result = get_cached_paper(doi)

        if result is None:
            elapsed = int((time.time() - start) * 1000)
            print(f"💨 Cache MISS for {doi} ({elapsed}ms)")
            return None

        elapsed = int((time.time() - start) * 1000)
        print(f"⚡ Cache HIT for {doi} ({elapsed}ms)")
        return result

    except Exception as e:
        print(f"⚠️  Cache lookup failed for {doi}: {e}")
        return None  # Fall through to live API calls


# CACHE STORE

def save_to_cache(doi: str, result: dict, oa_status: str = "unknown"):
    """
    Saves a result to the SQLite cache after a successful API call.
    Silently fails if storage fails — cache is best-effort.
    """
    try:
        store_paper(doi, result, oa_status)
        ttl = CACHE_TTL.get(oa_status, CACHE_TTL["unknown"])
        ttl_days = ttl // (24 * 60 * 60)
        print(f"💾 Cached {doi} — TTL: {ttl_days} days (oa_status: {oa_status})")

    except Exception as e:
        print(f"⚠️  Failed to cache {doi}: {e}")


# CACHE WRAPPER
async def get_with_cache(doi: str, fetch_fn) -> dict:
    """
    Cache-aside wrapper for any async fetch function.

    Usage:
        result = await get_with_cache(doi, lambda: query_all_sources(doi))

    Returns cached result immediately on hit.
    On miss, calls fetch_fn(), caches the result, and returns it.
    """
    # Step 1: Check cache
    cached = get_from_cache(doi)
    if cached:
        return cached

    # Step 2: Cache miss — call the live fetch function
    print(f"🌐 Fetching live data for {doi}")
    start = time.time()

    try:
        result = await fetch_fn()
        elapsed = int((time.time() - start) * 1000)
        print(f"✅ Live fetch completed in {elapsed}ms")

        if result:
            # Step 3: Store in cache
            oa_status = result.get("oa_status", "unknown")
            save_to_cache(doi, result, oa_status)

        return result

    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        print(f"❌ Live fetch failed for {doi} after {elapsed}ms: {e}")
        return {
            "doi": doi,
            "error": str(e),
            "partial_result": True,
            "free_sources": [],
            "cached": False
        }

# CACHE UTILITIES

def invalidate_cache(doi: str):
    """
    Forces a cache miss on next lookup by expiring the entry.
    Useful if you know data has changed.
    """
    try:
        import sqlite3
        import os
        DB_PATH = os.path.join(os.path.dirname(__file__), "paperpath.db")
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE papers SET cache_expires_at = datetime('now') WHERE doi = ?",
            (doi.lower().strip(),)
        )
        conn.commit()
        conn.close()
        print(f"🗑️  Cache invalidated for {doi}")
    except Exception as e:
        print(f"⚠️  Failed to invalidate cache for {doi}: {e}")


def get_cache_stats() -> dict:
    """
    Returns basic cache statistics for debugging.
    """
    try:
        import sqlite3
        import os
        DB_PATH = os.path.join(os.path.dirname(__file__), "paperpath.db")
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        total = conn.execute("SELECT COUNT(*) as n FROM papers").fetchone()["n"]
        valid = conn.execute(
            "SELECT COUNT(*) as n FROM papers WHERE cache_expires_at > datetime('now')"
        ).fetchone()["n"]
        expired = total - valid

        by_status = conn.execute(
            "SELECT oa_status, COUNT(*) as n FROM papers GROUP BY oa_status"
        ).fetchall()

        conn.close()

        return {
            "total_cached": total,
            "valid_entries": valid,
            "expired_entries": expired,
            "by_oa_status": {row["oa_status"]: row["n"] for row in by_status}
        }

    except Exception as e:
        return {"error": str(e)}