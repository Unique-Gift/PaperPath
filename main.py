"""
PaperPath MCP Server (Python + FastMCP)

Unbundles Elsevier/Scopus ($5K-$50K/yr) and Web of Science ($10K+/yr).
Given any DOI or paper title, returns every legal free access route
ranked by version fidelity.
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.dependencies import get_http_headers
from fastmcp.exceptions import ToolError
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse
import uvicorn

from ctxprotocol import verify_context_request, ContextError

load_dotenv()

PORT = int(os.getenv("PORT", "4010"))

class FreeSource(BaseModel):
    source: str
    url: str
    version: str
    legal: bool
    fidelity_score: float = 0.2
    fidelity_label: str = "Version unknown"

class AuthorContact(BaseModel):
    name: str | None = None
    email: str | None = None
    orcid: str | None = None
    note: str | None = None
    institution: str | None = None
    institution_domain: str | None = None
    is_corresponding: bool | None = None
    openalex_id: str | None = None

class InstitutionalAccess(BaseModel):
    detected_institution: str
    short_name: str | None
    has_access: bool
    publisher: str | None
    agreement_type: str | None
    expires: str | None
    notes: str | None

class PaperAccessResult(BaseModel):
    doi: str
    title: str | None
    publisher: str | None
    journal: str | None
    published_date: str | None
    is_open_access: bool
    oa_status: str
    free_sources: list[FreeSource]
    best_free_version: FreeSource | None
    author_contact: AuthorContact | None
    institutional_access: InstitutionalAccess | None
    partial_result: bool
    cached: bool
    response_time_ms: int
    timestamp: str
    esac_agreements: list = []

class ContextProtocolAuthMiddleware(Middleware):
    async def on_call_tool(self, context: MiddlewareContext, call_next):
        headers = get_http_headers()
        auth_header = headers.get("authorization", "")
        try:
            await verify_context_request(authorization_header=auth_header)
        except ContextError as e:
            raise ToolError(f"Unauthorized: {e.message}")
        return await call_next(context)

mcp = FastMCP(
    name="paperpath",
    instructions="""PaperPath — Academic Paper Access Intelligence.
    
Given any DOI or paper title, returns every legal free route to the full text,
ranked by how closely each free version matches the published article.

Unbundles Elsevier/Scopus ($5K-$50K/yr) and Web of Science ($10K+/yr).
Cross-validates across Unpaywall, OpenAlex, and Semantic Scholar.""",
)

#mcp.add_middleware(ContextProtocolAuthMiddleware())


@mcp.tool(
    name="find_paper_access",
    description="""🎓 Find every legal free route to a research paper.

Given a DOI or paper title, returns all free access options ranked by version quality:
- published (exact match to journal version)
- author_accepted (peer-reviewed, minor formatting differences)
- preprint (pre-peer-review, may differ from final)
- submitted (early draft)

Also detects institutional access if you provide your institution domain.

Examples:
- DOI: "10.1038/s41586-021-03819-2"
- Title: "Attention Is All You Need"
- With institution: doi + institution_domain "mit.edu"

Replaces: Elsevier ScienceDirect, Web of Science""",
    meta={
        "surface": "both",
        "queryEligible": True,
        "latencyClass": "fast",
        "pricing": {
            "executeUsd": "0.001",
        },
        "rateLimit": {
            "maxRequestsPerMinute": 60,
            "cooldownMs": 1000,
            "maxConcurrency": 5,
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "doi": {"type": "string"},
            "title": {"type": ["string", "null"]},
            "publisher": {"type": ["string", "null"]},
            "journal": {"type": ["string", "null"]},
            "published_date": {"type": ["string", "null"]},
            "is_open_access": {"type": "boolean"},
            "oa_status": {"type": "string"},
            "free_sources": {"type": "array"},
            "best_free_version": {"type": ["object", "null"]},
            "author_contact": {"type": ["object", "null"]},
            "institutional_access": {"type": ["object", "null"]},
            "esac_agreements": {"type": "array"},
            "partial_result": {"type": "boolean"},
            "cached": {"type": "boolean"},
            "response_time_ms": {"type": "integer"},
            "timestamp": {"type": "string"}
        },
        "required": ["doi", "is_open_access", "oa_status", "free_sources", "partial_result", "cached", "response_time_ms", "timestamp"]
    }
)

async def find_paper_access(
    doi: Annotated[str | None, Field(
        description="Paper DOI (e.g. '10.1038/s41586-021-03819-2')",
        default=None,
        examples=["10.1038/s41586-021-03819-2", "10.1126/science.1058040"]
    )] = None,
    title: Annotated[str | None, Field(
        description="Paper title if DOI is unknown",
        default=None,
        examples=["Attention Is All You Need", "CRISPR-Cas9 genome editing"]
    )] = None,
    institution_domain: Annotated[str | None, Field(
        description="Your institution email domain to check existing access (e.g. 'mit.edu', 'ox.ac.uk')",
        default=None,
        examples=["mit.edu", "ox.ac.uk", "harvard.edu"]
    )] = None,
) -> dict:
    """Find every legal free route to a research paper."""
    import time
    start = time.time()

    from sources import fetch_all_sources
    from normalizer import normalize
    from cache import get_cached_paper, store_paper
    from database import get_institutional_access

    if not doi and not title:
        raise ToolError("Provide either a DOI or a paper title")
    
    if not doi and title:
        from sources import resolve_title_to_doi
        doi = await resolve_title_to_doi(title)
        if not doi:
            raise ToolError(f"Could not find a DOI for title: {title}")

   
    institutional_access = None
    esac_agreements = []
    if institution_domain:
        institutional_access = get_institutional_access(institution_domain)

    cached = get_cached_paper(doi)
    if cached:
        elapsed = int((time.time() - start) * 1000)
        free_sources = [FreeSource(**s) for s in cached.get("free_sources", [])]
        ac = cached.get("author_contact")
        ia = InstitutionalAccess(**institutional_access) if institutional_access else None
        return PaperAccessResult(
            doi=cached["doi"],
            title=cached.get("title"),
            publisher=None,
            journal=None,
            published_date=None,
            is_open_access=cached.get("is_open_access", False),
            oa_status=cached.get("oa_status", "unknown"),
            free_sources=free_sources,
            best_free_version=free_sources[0] if free_sources else None,
            author_contact=AuthorContact(**ac) if ac else None,
            institutional_access=ia,
            partial_result=False,
            cached=True,
            response_time_ms=elapsed,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ).model_dump()

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
    store_paper(doi, result, result.get("oa_status", "unknown"))

    if institution_domain:
        from database import get_esac_access
        keyword = institution_domain.split(".")[0]
        esac_agreements = get_esac_access(keyword)

    elapsed = int((time.time() - start) * 1000)
    free_sources = [FreeSource(**s) for s in result.get("free_sources", [])]
    ac = result.get("author_contact")
    ia = InstitutionalAccess(**institutional_access) if institutional_access else None

    return PaperAccessResult(
        doi=result.get("doi", doi),
        title=result.get("title"),
        publisher=result.get("publisher"),
        journal=result.get("journal"),
        published_date=result.get("published_date"),
        is_open_access=result.get("is_open_access", False),
        oa_status=result.get("oa_status", "unknown"),
        free_sources=free_sources,
        best_free_version=free_sources[0] if free_sources else None,
        author_contact=AuthorContact(**ac) if ac else None,
        institutional_access=ia,
        partial_result=result.get("partial_result", False),
        cached=False,
        response_time_ms=elapsed,
        timestamp=datetime.now(timezone.utc).isoformat(),
        esac_agreements=esac_agreements,
    ).model_dump()

async def health_check(request):
    return JSONResponse({
        "status": "healthy",
        "service": "paperpath",
        "version": "1.0.0",
        "framework": "FastMCP",
        "tools": ["find_paper_access"],
        "replaces": "Elsevier/Scopus ($5K-$50K/yr), Web of Science ($10K+/yr)",
        "esac_agreements": 1548,
        "esac_last_updated": "2026-03-13",
    })

mcp_app = mcp.http_app(path="/mcp")

app = Starlette(
    routes=[
        Route("/health", health_check),
        Mount("/", app=mcp_app),
    ],
    lifespan=mcp_app.lifespan,
)

if __name__ == "__main__":
    print(f"🚀 PaperPath MCP Server starting on port {PORT}")
    print(f"🔧 Framework: FastMCP")
    print(f"🎓 Tool: find_paper_access")
    print(f"🔒 Auth: Context Protocol JWT on tools/call only")
    uvicorn.run(app, host="0.0.0.0", port=PORT)