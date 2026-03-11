"""
PaperPath Test Suite
Run with: python tests/test_all.py
"""

import asyncio
import sys
sys.path.insert(0, ".")

# TEST DOIs

TEST_DOIS = [
    "10.1038/s41586-021-03819-2",       # AlphaFold — hybrid OA
    "10.5281/zenodo.17373899",           # Zenodo — green OA
    "10.1016/j.indmarman.2020.03.003",  # Elsevier — closed
    "10.52589/BJMMSOVPW9YV5",           # African journal
    "10.1126/science.1058040",           # Science journal
]

# TEST 1: SOURCES

async def test_sources():
    print("\n" + "="*60)
    print("TEST 1: SOURCES (fetch_all_sources)")
    print("="*60)

    from sources import fetch_all_sources

    for doi in TEST_DOIS[:2]:
        print(f"\nDOI: {doi}")
        result = await fetch_all_sources(doi)
        print(f"  Sources available: {result['sources_available']}")
        print(f"  Response time: {result['total_response_time_ms']}ms")
        print(f"  Partial: {result['partial_result']}")

# TEST 2: NORMALIZER

async def test_normalizer():
    print("\n" + "="*60)
    print("TEST 2: NORMALIZER")
    print("="*60)

    from sources import fetch_all_sources
    from normalizer import normalize

    for doi in TEST_DOIS[:2]:
        print(f"\nDOI: {doi}")
        raw = await fetch_all_sources(doi)
        result = normalize(
            doi=doi,
            unpaywall=raw.get("unpaywall"),
            openalex=raw.get("openalex"),
            semantic_scholar=raw.get("semantic_scholar"),
            sources_available=raw.get("sources_available") or [],
            sources_failed=raw.get("sources_failed") or [],
            response_time_ms=raw.get("total_response_time_ms", 0)
        )
        print(f"  Title: {result['title']}")
        print(f"  OA Status: {result['oa_status']}")
        print(f"  Free sources: {len(result['free_sources'])}")
        if result['free_sources']:
            best = result['free_sources'][0]
            print(f"  Best: {best['version']} — score {best.get('fidelity_score', 'N/A')}")
        print(f"  Response time: {result['response_time_ms']}ms")

# TEST 3: CACHE

async def test_cache():
    print("\n" + "="*60)
    print("TEST 3: CACHE")
    print("="*60)

    from cache import get_cached_paper, store_paper, get_cache_stats

    test_result = {
        "doi": "10.9999/test",
        "title": "Test Paper",
        "is_open_access": True,
        "oa_status": "gold",
        "free_sources": [{"source": "Test", "url": "https://test.com", "version": "published", "legal": True}],
        "author_contact": {"name": "Test Author", "email": None, "orcid": None}
    }

    print("\n  Storing test paper...")
    store_paper("10.9999/test", test_result, "gold")

    print("  Fetching from cache...")
    cached = get_cached_paper("10.9999/test")
    print(f"  Cache hit: {cached is not None}")

    stats = get_cache_stats()
    print(f"  Cache stats: {stats}")

# TEST 4: CIRCUIT BREAKER

async def test_circuit_breaker():
    print("\n" + "="*60)
    print("TEST 4: CIRCUIT BREAKER")
    print("="*60)

    from circuit_breaker import get_state, record_failure, record_success, should_skip, get_circuit_status

    print(f"  Initial state: {get_state('unpaywall')}")

    record_failure("unpaywall", "timeout")
    record_failure("unpaywall", "timeout")
    record_failure("unpaywall", "timeout")
    print(f"  After 3 failures: {get_state('unpaywall')}")
    print(f"  Should skip: {should_skip('unpaywall')}")

    record_success("unpaywall")
    print(f"  After success: {get_state('unpaywall')}")

    status = get_circuit_status()
    print(f"  All circuits: {status}")

# TEST 5: INSTITUTIONAL ACCESS

async def test_institutional_access():
    print("\n" + "="*60)
    print("TEST 5: INSTITUTIONAL ACCESS")
    print("="*60)

    from database import get_institutional_access

    for domain in ["mit.edu", "ox.ac.uk", "harvard.edu", "unilag.edu.ng", "unknown.edu"]:
        result = get_institutional_access(domain)
        if result:
            print(f"  {domain} → {result['detected_institution']} — {result['publisher']}")
        else:
            print(f"  {domain} → No institutional access found")

# RUN ALL TESTS

async def run_all():
    await test_sources()
    await test_normalizer()
    await test_cache()
    await test_circuit_breaker()
    await test_institutional_access()
    print("\n" + "="*60)
    print("✅ All tests complete")
    print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(run_all())
