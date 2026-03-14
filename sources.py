from dotenv import load_dotenv
load_dotenv()

import httpx
import asyncio
import os
import time
from typing import Optional
from database import log_api_call


UNPAYWALL_EMAIL = os.getenv("UNPAYWALL_EMAIL")

API_TIMEOUT = 5.0

async def fetch_unpaywall(doi: str) -> Optional[dict]:
    """
    Fetches open access status and free PDF links for a DOI.
    Returns normalized dict or None on failure.
    """
    url = f"https://api.unpaywall.org/v2/{doi}?email={UNPAYWALL_EMAIL}"
    start = time.time()

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.get(url)
            elapsed = int((time.time() - start) * 1000)

            log_api_call(
                source="unpaywall",
                query=doi,
                status_code=response.status_code,
                response_time_ms=elapsed,
                success=response.status_code == 200
            )

            if response.status_code != 200:
                return None

            data = response.json()

            best_location = data.get("best_oa_location")
            free_sources = []

            if best_location and best_location.get("url"):
                free_sources.append({
                    "source": "Unpaywall",
                    "url": best_location.get("url_for_pdf") or best_location.get("url"),
                    "version": normalize_version(best_location.get("version", "")),
                    "legal": True
                })

            for location in data.get("oa_locations", []):
                url_pdf = location.get("url_for_pdf") or location.get("url")
                if url_pdf and url_pdf != (best_location or {}).get("url_for_pdf"):
                    free_sources.append({
                        "source": f"Unpaywall ({location.get('host_type', 'unknown')})",
                        "url": url_pdf,
                        "version": normalize_version(location.get("version", "")),
                        "legal": True
                    })

            return {
                "source": "unpaywall",
                "doi": data.get("doi"),
                "title": data.get("title"),
                "is_open_access": data.get("is_oa", False),
                "oa_status": data.get("oa_status"),
                "publisher": data.get("publisher"),
                "journal": data.get("journal_name"),
                "free_sources": free_sources,
                "response_time_ms": elapsed
            }

    except httpx.TimeoutException:
        elapsed = int((time.time() - start) * 1000)
        log_api_call("unpaywall", doi, None, elapsed, False, "Timeout")
        print(f"⚠️  Unpaywall timed out for {doi}")
        return None

    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        log_api_call("unpaywall", doi, None, elapsed, False, str(e))
        print(f"⚠️  Unpaywall error for {doi}: {e}")
        return None

async def fetch_openalex(doi: str) -> Optional[dict]:
    """
    Fetches paper metadata, author details, and institutional affiliations.
    Returns normalized dict or None on failure.
    """
    url = f"https://api.openalex.org/works/https://doi.org/{doi}"
    start = time.time()

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "PaperPath/1.0 (mailto:your@email.com)"}
            )
            elapsed = int((time.time() - start) * 1000)

            log_api_call(
                source="openalex",
                query=doi,
                status_code=response.status_code,
                response_time_ms=elapsed,
                success=response.status_code == 200
            )

            if response.status_code != 200:
                return None

            data = response.json()
            authors = []
            corresponding_author = None

            for authorship in data.get("authorships", []):
                author_data = authorship.get("author", {})
                institutions = authorship.get("institutions", [])

                author = {
                    "name": author_data.get("display_name"),
                    "orcid": author_data.get("orcid"),
                    "openalex_id": author_data.get("id"),
                    "institution": institutions[0].get("display_name") if institutions else None,
                    "institution_domain": extract_domain_from_ror(
                        institutions[0].get("ror") if institutions else None
                    ),
                    "is_corresponding": authorship.get("is_corresponding", False)
                }

                authors.append(author)

                if authorship.get("is_corresponding") and not corresponding_author:
                    corresponding_author = author

            if not corresponding_author and authors:
                corresponding_author = authors[0]

            free_sources = []
            oa_data = data.get("open_access", {})
            if oa_data.get("oa_url"):
                free_sources.append({
                    "source": "OpenAlex",
                    "url": oa_data.get("oa_url"),
                    "version": "published" if oa_data.get("oa_status") == "gold" else "author_accepted",
                    "legal": True
                })

            return {
                "source": "openalex",
                "doi": doi,
                "title": data.get("display_name") or data.get("title"),
                "is_open_access": oa_data.get("is_oa", False),
                "oa_status": oa_data.get("oa_status"),
                "publisher": data.get("primary_location", {}).get("source", {}).get("publisher") if data.get("primary_location") else None,
                "journal": data.get("primary_location", {}).get("source", {}).get("display_name") if data.get("primary_location") else None,
                "published_date": data.get("publication_date"),
                "authors": authors,
                "corresponding_author": corresponding_author,
                "free_sources": free_sources,
                "response_time_ms": elapsed
            }

    except httpx.TimeoutException:
        elapsed = int((time.time() - start) * 1000)
        log_api_call("openalex", doi, None, elapsed, False, "Timeout")
        print(f"⚠️  OpenAlex timed out for {doi}")
        return None

    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        log_api_call("openalex", doi, None, elapsed, False, str(e))
        print(f"⚠️  OpenAlex error for {doi}: {e}")
        return None

async def fetch_semantic_scholar(doi: str) -> Optional[dict]:
    """
    Fetches paper data including open access PDF links and author info.
    Returns normalized dict or None on failure.
    """
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/{doi}"
        f"?fields=title,authors,openAccessPdf,externalIds,publicationTypes,journal"
    )
    start = time.time()

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.get(url)
            elapsed = int((time.time() - start) * 1000)

            log_api_call(
                source="semantic_scholar",
                query=doi,
                status_code=response.status_code,
                response_time_ms=elapsed,
                success=response.status_code == 200
            )

            if response.status_code != 200:
                return None

            data = response.json()

            free_sources = []
            oa_pdf = data.get("openAccessPdf")
            if oa_pdf and oa_pdf.get("url"):
                free_sources.append({
                    "source": "Semantic Scholar",
                    "url": oa_pdf.get("url"),
                    "version": "author_accepted",
                    "legal": True
                })
            authors = []
            for author in data.get("authors", []):
                authors.append({
                    "name": author.get("name"),
                    "semantic_scholar_id": author.get("authorId")
                })

            return {
                "source": "semantic_scholar",
                "doi": doi,
                "title": data.get("title"),
                "journal": data.get("journal", {}).get("name") if data.get("journal") else None,
                "authors": authors,
                "free_sources": free_sources,
                "response_time_ms": elapsed
            }

    except httpx.TimeoutException:
        elapsed = int((time.time() - start) * 1000)
        log_api_call("semantic_scholar", doi, None, elapsed, False, "Timeout")
        print(f"⚠️  Semantic Scholar timed out for {doi}")
        return None

    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        log_api_call("semantic_scholar", doi, None, elapsed, False, str(e))
        print(f"⚠️  Semantic Scholar error for {doi}: {e}")
        return None
    
async def resolve_title_to_doi(title: str) -> Optional[str]:
    """
    Resolves a paper title to a DOI.
    Strategy 1: OpenAlex search
    Strategy 2: Crossref search (better title matching)
    """
    clean_title = title.strip()

    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:

        try:
            query = " ".join(clean_title.lower().split()[:10])
            url = f"https://api.openalex.org/works?search={query}&per_page=3"
            response = await client.get(
                url,
                headers={"User-Agent": "PaperPath/1.0 (mailto:amahuniquegift@gmail.com)"}
            )
            if response.status_code == 200:
                results = response.json().get("results", [])
                query_words = set(clean_title.lower().split()) - {"a", "an", "the", "and", "of", "in", "for", "on", "with", "to"}
                for result in results:
                    result_title = (result.get("display_name") or "").lower()
                    match_count = sum(1 for w in query_words if w in result_title)
                    if len(query_words) > 0 and match_count / len(query_words) >= 0.6:
                        doi = result.get("doi", "")
                        if doi:
                            print(f"✅ OpenAlex resolved: {doi}")
                            return doi.replace("https://doi.org/", "")
        except Exception as e:
            print(f"⚠️  OpenAlex title resolution failed: {e}")

        try:
            import urllib.parse
            encoded = urllib.parse.quote(clean_title)
            url = f"https://api.crossref.org/works?query.title={encoded}&rows=3"
            response = await client.get(
                url,
                headers={"User-Agent": "PaperPath/1.0 (mailto:amahuniquegift@gmail.com)"}
            )
            if response.status_code == 200:
                items = response.json().get("message", {}).get("items", [])
                query_words = set(clean_title.lower().split()) - {"a", "an", "the", "and", "of", "in", "for", "on", "with", "to"}
                for item in items:
                    titles = item.get("title", [])
                    item_title = titles[0].lower() if titles else ""
                    match_count = sum(1 for w in query_words if w in item_title)
                    if len(query_words) > 0 and match_count / len(query_words) >= 0.4:
                        doi = item.get("DOI", "")
                        if doi:
                            print(f"✅ Crossref resolved: {doi}")
                            return doi
        except Exception as e:
            print(f"⚠️  Crossref title resolution failed: {e}")

    print(f"⚠️  Could not resolve title to DOI: {title}")
    return None

async def fetch_all_sources(doi: str) -> dict:
    """
    Fires all 3 API calls in parallel.
    Returns dict with results from each source.
    Failed sources return None — never crashes the whole response.
    """
    print(f"🔍 Querying all sources for DOI: {doi}")
    start = time.time()

    unpaywall_result, openalex_result, semantic_result = await asyncio.gather(
        fetch_unpaywall(doi),
        fetch_openalex(doi),
        fetch_semantic_scholar(doi),
        return_exceptions=True 
    )
    if isinstance(unpaywall_result, Exception):
        print(f"⚠️  Unpaywall exception: {unpaywall_result}")
        unpaywall_result = None

    if isinstance(openalex_result, Exception):
        print(f"⚠️  OpenAlex exception: {openalex_result}")
        openalex_result = None

    if isinstance(semantic_result, Exception):
        print(f"⚠️  Semantic Scholar exception: {semantic_result}")
        semantic_result = None
    elapsed = int((time.time() - start) * 1000)
    sources_available = [
        name for name, result in [
            ("unpaywall", unpaywall_result),
            ("openalex", openalex_result),
            ("semantic_scholar", semantic_result),
        ] if result is not None
    ]   
    sources_failed = [
        name for name, result in [
            ("unpaywall", unpaywall_result),
            ("openalex", openalex_result),
            ("semantic_scholar", semantic_result),
        ] if result is None
    ]   
    print(f"✅ Sources returned: {len(sources_available)}/3 in {elapsed}ms")

    return {
        "unpaywall": unpaywall_result,
        "openalex": openalex_result,
        "semantic_scholar": semantic_result,
        "sources_available": sources_available,
        "sources_failed": sources_failed,
        "total_response_time_ms": elapsed,
        "partial_result": len(sources_available) < 3
    }

def normalize_version(version_str: str) -> str:
    """Normalizes version strings from different APIs into our schema types."""
    version_str = (version_str or "").lower()
    if "publishedversion" in version_str or "published" in version_str:
        return "published"
    elif "acceptedversion" in version_str or "accepted" in version_str:
        return "author_accepted"
    elif "submittedversion" in version_str or "submitted" in version_str:
        return "submitted"
    else:
        return "preprint"


def extract_domain_from_ror(ror_url: Optional[str]) -> Optional[str]:
    """
    Placeholder — in a full implementation this would look up
    the institution domain from a ROR ID.
    For now returns None.
    """
    return None