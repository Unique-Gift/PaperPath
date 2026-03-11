from typing import Optional
import time

# VERSION PRIORITY
# Higher number = better version

VERSION_PRIORITY = {
    "published":       4,
    "author_accepted": 3,
    "preprint":        2,
    "submitted":       1,
    "unknown":         0
}
FIDELITY_SCORES = {
    "published": {
        "score": 1.0,
        "label": "Exact match to journal version"
    },
    "author_accepted": {
        "score": 0.85,
        "label": "Peer-reviewed final draft — same content, minor typesetting differences"
    },
    "preprint": {
        "score": 0.6,
        "label": "Pre-peer-review version — may differ from final published article"
    },
    "submitted": {
        "score": 0.4,
        "label": "Early draft — significant differences likely from published version"
    },
    "unknown": {
        "score": 0.2,
        "label": "Version unknown — verify against published article"
    },
}
# HELPERS

def normalize_version(raw: str) -> str:
    """
    Maps raw version strings from APIs to our standard types.
    """
    if not raw:
        return "unknown"

    raw = raw.lower().strip()

    if any(x in raw for x in ["publishedversion", "published", "final"]):
        return "published"
    if any(x in raw for x in ["acceptedmanuscript", "accepted", "author_accepted", "postprint"]):
        return "author_accepted"
    if any(x in raw for x in ["preprint", "submittedversion", "arxiv", "biorxiv", "medrxiv"]):
        return "preprint"
    if "submitted" in raw:
        return "submitted"

    return "unknown"


def best_source(sources: list) -> Optional[dict]:
    """
    Returns the highest quality free source from a list.
    Priority: published > author_accepted > preprint > submitted
    """
    if not sources:
        return None

    return max(
        sources,
        key=lambda s: VERSION_PRIORITY.get(s.get("version", "unknown"), 0)
    )


# SOURCE PARSERS
# Each function extracts what we need from a raw API response

def parse_unpaywall(raw: dict) -> dict:
    """
    Extracts free sources and OA status from Unpaywall response.
    """
    if not raw:
        return {}

    free_sources = []

    # Best OA location
    best = raw.get("best_oa_location") or {}
    if best.get("url"):
        free_sources.append({
            "source": "Unpaywall",
            "url": best.get("url_for_pdf") or best.get("url"),
            "version": normalize_version(best.get("version", "")),
            "legal": True,
            "host_type": best.get("host_type", "unknown")
        })

    # All OA locations
    for loc in raw.get("oa_locations", []):
        url = loc.get("url_for_pdf") or loc.get("url")
        if url and url != (best.get("url_for_pdf") or best.get("url")):
            free_sources.append({
                "source": f"Unpaywall ({loc.get('host_type', 'unknown')})",
                "url": url,
                "version": normalize_version(loc.get("version", "")),
                "legal": True,
                "host_type": loc.get("host_type", "unknown")
            })

    return {
        "title": raw.get("title"),
        "doi": raw.get("doi"),
        "is_open_access": raw.get("is_oa", False),
        "oa_status": raw.get("oa_status", "unknown"),
        "publisher": raw.get("publisher"),
        "journal": raw.get("journal_name"),
        "published_date": raw.get("published_date") or raw.get("year"),
        "free_sources": free_sources
    }


def parse_openalex(raw: dict) -> dict:
    """
    Extracts metadata, author info, and free sources from OpenAlex response.
    """
    if not raw:
        return {}

    free_sources = []

    # Primary location
    primary = raw.get("primary_location") or {}
    landing = primary.get("landing_page_url")
    pdf = primary.get("pdf_url")

    if pdf:
        free_sources.append({
            "source": "OpenAlex",
            "url": pdf,
            "version": normalize_version(primary.get("version", "")),
            "legal": True
        })
    elif landing and raw.get("open_access", {}).get("is_oa"):
        free_sources.append({
            "source": "OpenAlex",
            "url": landing,
            "version": "unknown",
            "legal": True
        })

    # Best OA URL
    oa_url = raw.get("open_access", {}).get("oa_url")
    if oa_url and oa_url not in [s["url"] for s in free_sources]:
        free_sources.append({
            "source": "OpenAlex (OA)",
            "url": oa_url,
            "version": "unknown",
            "legal": True
        })

    # Extract corresponding author
    author_contact = None
    for authorship in raw.get("authorships", []):
        if authorship.get("is_corresponding"):
            author = authorship.get("author", {})
            institutions = authorship.get("institutions", [])
            inst_name = institutions[0].get("display_name") if institutions else None

            author_contact = {
                "name": author.get("display_name"),
                "orcid": author.get("orcid"),
                "institution": inst_name,
                "email": None  # OpenAlex doesn't expose emails directly
            }
            break

    # Fallback: first author if no corresponding author found
    if not author_contact and raw.get("authorships"):
        first = raw["authorships"][0]
        author = first.get("author", {})
        institutions = first.get("institutions", [])
        inst_name = institutions[0].get("display_name") if institutions else None
        author_contact = {
            "name": author.get("display_name"),
            "orcid": author.get("orcid"),
            "institution": inst_name,
            "email": None
        }

    # Extract source/journal info
    source_info = primary.get("source") or {}

    return {
        "title": raw.get("display_name") or raw.get("title"),
        "doi": raw.get("doi", "").replace("https://doi.org/", ""),
        "is_open_access": raw.get("open_access", {}).get("is_oa", False),
        "oa_status": raw.get("open_access", {}).get("oa_status", "unknown"),
        "publisher": source_info.get("host_organization_name"),
        "journal": source_info.get("display_name"),
        "published_date": raw.get("publication_date"),
        "citation_count": raw.get("cited_by_count", 0),
        "author_count": len(raw.get("authorships", [])),
        "author_contact": author_contact,
        "free_sources": free_sources
    }


def parse_semantic_scholar(raw: dict) -> dict:
    """
    Extracts free PDF and author info from Semantic Scholar response.
    """
    if not raw:
        return {}

    free_sources = []

    # Open access PDF
    oa_pdf = raw.get("openAccessPdf")
    if oa_pdf and oa_pdf.get("url"):
        free_sources.append({
            "source": "Semantic Scholar",
            "url": oa_pdf["url"],
            "version": normalize_version(oa_pdf.get("status", "")),
            "legal": True
        })

    # Author contact
    author_contact = None
    authors = raw.get("authors", [])
    if authors:
        first = authors[0]
        author_contact = {
            "name": first.get("name"),
            "orcid": None,
            "institution": None,
            "email": None
        }

    return {
        "title": raw.get("title"),
        "doi": raw.get("externalIds", {}).get("DOI"),
        "is_open_access": bool(oa_pdf),
        "author_contact": author_contact,
        "free_sources": free_sources
    }

# MAIN NORMALIZER
# Merges parsed results from all 3 sources into one clean output

def normalize(
    doi: str,
    unpaywall: Optional[dict] = None,
    openalex: Optional[dict] = None,
    semantic_scholar: Optional[dict] = None,
    sources_available: list = [],
    sources_failed: list = [],
    response_time_ms: int = 0
) -> dict:
    """
    Merges results from all available sources into one clean response.

    Priority for each field:
    - title:          Unpaywall > OpenAlex > Semantic Scholar
    - oa_status:      Unpaywall > OpenAlex
    - publisher:      Unpaywall > OpenAlex
    - author_contact: OpenAlex > Semantic Scholar
    - free_sources:   merged from all, deduplicated, sorted by version quality
    """

    # Parse each raw source
    parsed_unpaywall         = unpaywall         if unpaywall         else {}
    parsed_openalex          = openalex          if openalex          else {}
    parsed_semantic_scholar  = semantic_scholar  if semantic_scholar  else {}

    # --- TITLE ---
    title = (
        parsed_unpaywall.get("title") or
        parsed_openalex.get("title") or
        parsed_semantic_scholar.get("title") or
        "Unknown"
    )

    # --- OA STATUS ---
    oa_status = (
        parsed_unpaywall.get("oa_status") or
        parsed_openalex.get("oa_status") or
        "unknown"
    )

    # --- IS OPEN ACCESS ---
    is_open_access = (
        parsed_unpaywall.get("is_open_access") or
        parsed_openalex.get("is_open_access") or
        parsed_semantic_scholar.get("is_open_access") or
        False
    )

    # --- PUBLISHER / JOURNAL ---
    publisher = (
        parsed_unpaywall.get("publisher") or
        parsed_openalex.get("publisher")
    )
    journal = (
        parsed_unpaywall.get("journal") or
        parsed_openalex.get("journal")
    )

    # --- PUBLISHED DATE ---
    published_date = (
        parsed_unpaywall.get("published_date") or
        parsed_openalex.get("published_date")
    )

    # --- CITATION COUNT ---
    citation_count = parsed_openalex.get("citation_count", 0)

    # --- MERGE FREE SOURCES ---
    all_sources = (
        parsed_unpaywall.get("free_sources", []) +
        parsed_openalex.get("free_sources", []) +
        parsed_semantic_scholar.get("free_sources", [])
    )

    # Deduplicate by URL
    # Deduplicate by URL and add fidelity scores
    seen_urls = set()
    unique_sources = []
    for s in all_sources:
        url = s.get("url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            version = s.get("version", "unknown")
            fidelity = FIDELITY_SCORES.get(version, FIDELITY_SCORES["unknown"])
            s["fidelity_score"] = fidelity["score"]
            s["fidelity_label"] = fidelity["label"]
            unique_sources.append(s)

    # Sort by version quality (best first)
    unique_sources.sort(
        key=lambda s: VERSION_PRIORITY.get(s.get("version", "unknown"), 0),
        reverse=True
    )

    # --- BEST FREE VERSION ---
    best = best_source(unique_sources)

    # --- AUTHOR CONTACT ---
    # OpenAlex has institution info, Semantic Scholar has name
    author_contact = (
        parsed_openalex.get("corresponding_author") or
        parsed_openalex.get("author_contact") or
        parsed_semantic_scholar.get("author_contact")
    )

    # Add email hint if we have institution
    if author_contact and not author_contact.get("email"):
        author_contact["note"] = "Email not publicly available — contact via institution or ResearchGate"

    # --- ASSEMBLE FINAL RESPONSE ---
    return {
        "doi": doi,
        "title": title,
        "publisher": publisher,
        "journal": journal,
        "published_date": str(published_date) if published_date else None,
        "citation_count": citation_count,
        "is_open_access": is_open_access,
        "oa_status": oa_status,
        "free_sources": unique_sources,
        "best_free_version": best,
        "author_contact": author_contact,
        "sources_queried": list(sources_available or []) + list(sources_failed or []),
        "sources_available": sources_available,
        "sources_failed": sources_failed,
        "partial_result": len(sources_failed) > 0,
        "cached": False,
        "response_time_ms": response_time_ms,
        "institutional_access": None
    }


# TEST
if __name__ == "__main__":
    import asyncio
    import sys
    sys.path.insert(0, ".")
    from sources import fetch_all_sources

    async def run_test():
        print("Testing normalizer...\n")
        test_doi = "10.1038/s41586-021-03819-2"

        start = time.time()
        raw = await fetch_all_sources(test_doi)
        elapsed = int((time.time() - start) * 1000)

        result = normalize(
            doi=test_doi,
            unpaywall=raw.get("unpaywall"),
            openalex=raw.get("openalex"),
            semantic_scholar=raw.get("semantic_scholar"),
            sources_available=["unpaywall", "openalex", "semantic_scholar"],
            sources_failed=[],
            response_time_ms=raw.get("total_response_time_ms", elapsed)
        )

        print(f"DOI:              {result['doi']}")
        print(f"Title:            {result['title']}")
        print(f"Publisher:        {result['publisher']}")
        print(f"Journal:          {result['journal']}")
        print(f"OA Status:        {result['oa_status']}")
        print(f"Is Open Access:   {result['is_open_access']}")
        print(f"Free Sources:     {len(result['free_sources'])}")
        print(f"Best Version:     {result['best_free_version']}")
        print(f"Author Contact:   {result['author_contact']}")
        print(f"Sources Queried:  {result['sources_queried']}")
        print(f"Partial Result:   {result['partial_result']}")
        print(f"Response Time:    {result['response_time_ms']}ms")
        print(f"Cached:           {result['cached']}")

    asyncio.run(run_test())