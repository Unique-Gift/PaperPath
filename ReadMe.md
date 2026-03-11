# PaperPath

**Find every legal free route to any research paper.**

PaperPath is an MCP server that unbundles Elsevier/Scopus ($5K–$50K/yr) and Web of Science ($10K+/yr). Given any DOI or paper title, it returns every legal free access route ranked by how closely each version matches the published article.

Live endpoint: `https://paperpath.up.railway.app`

---

## What It Does

Researchers pay $57 to access a 2-page study. Universities pay millions for subscriptions. PaperPath cross-validates across 3 independent sources and returns the best free version for $0.10/query.

**Input:** A DOI or paper title  
**Output:** Every legal free route ranked by version fidelity

```json
{
  "doi": "10.1038/s41586-021-03819-2",
  "title": "Highly accurate protein structure prediction with AlphaFold",
  "oa_status": "hybrid",
  "free_sources": [
    {
      "source": "Unpaywall",
      "url": "https://www.nature.com/articles/s41586-021-03819-2.pdf",
      "version": "published",
      "legal": true,
      "fidelity_score": 1.0,
      "fidelity_label": "Exact match to journal version"
    }
  ],
  "best_free_version": { ... },
  "author_contact": {
    "name": "John Jumper",
    "institution": "DeepMind",
    "orcid": "https://orcid.org/0000-0001-6169-6580"
  },
  "institutional_access": {
    "detected_institution": "Massachusetts Institute of Technology",
    "has_access": true,
    "publisher": "Elsevier"
  },
  "response_time_ms": 1352
}
```

---

## Architecture

```
Request (DOI or title)
        │
        ▼
┌───────────────────┐
│   Cache Check     │  SQLite — returns in <10ms if cached
└───────────────────┘
        │ MISS
        ▼
┌─────────────────────────────────────┐
│         Parallel API Calls          │
│  Unpaywall │ OpenAlex │ Sem. Scholar │  ~1-4 seconds
└─────────────────────────────────────┘
        │
        ▼
┌───────────────────┐
│  Circuit Breaker  │  Skips failed sources, never crashes
└───────────────────┘
        │
        ▼
┌───────────────────┐
│    Normalizer     │  Merges, deduplicates, ranks by fidelity
└───────────────────┘
        │
        ▼
┌───────────────────┐
│   Cache + Return  │  Stores result, returns clean JSON
└───────────────────┘
```

---

## Files

| File                 | Purpose                                                            |
| -------------------- | ------------------------------------------------------------------ |
| `main.py`            | FastMCP server, Pydantic models, Context Protocol middleware       |
| `sources.py`         | Parallel async calls to Unpaywall, OpenAlex, Semantic Scholar      |
| `normalizer.py`      | Merges 3 API responses, deduplicates, ranks by fidelity score      |
| `circuit_breaker.py` | Per-source failure handling with CLOSED/OPEN/HALF states           |
| `cache.py`           | SQLite caching with smart TTLs by OA status                        |
| `database.py`        | All DB operations including institutional access lookup            |
| `schema.sql`         | 9-table schema: papers, sources, authors, institutions, agreements |

---

## Data Sources

| Source                                                          | Coverage                            | Auth              |
| --------------------------------------------------------------- | ----------------------------------- | ----------------- |
| [Unpaywall](https://unpaywall.org/products/api)                 | 50M+ papers, OA status + PDF links  | Email only (free) |
| [OpenAlex](https://openalex.org/)                               | 250M+ works, metadata + author info | None              |
| [Semantic Scholar](https://www.semanticscholar.org/product/api) | 200M+ papers, CS/AI coverage        | None              |

---

## Version Fidelity Scoring

Every free source is scored on how closely it matches the published article:

| Version           | Score | Meaning                                      |
| ----------------- | ----- | -------------------------------------------- |
| `published`       | 1.0   | Exact match to journal version               |
| `author_accepted` | 0.85  | Peer-reviewed, minor typesetting differences |
| `preprint`        | 0.60  | Pre-peer-review, may differ from final       |
| `submitted`       | 0.40  | Early draft, significant differences likely  |
| `unknown`         | 0.20  | Version unknown, verify manually             |

---

## Institutional Access Detection

If you provide your institution domain, PaperPath checks whether you already have free access through your library before returning preprint links.

```json
{
  "institution_domain": "mit.edu"
}
```

Returns:

```json
{
  "institutional_access": {
    "detected_institution": "Massachusetts Institute of Technology",
    "has_access": true,
    "publisher": "Elsevier",
    "agreement_type": "read_and_publish"
  }
}
```

V2 will ingest the full [ESAC registry](https://esac-initiative.org/about/transformative-agreements/agreement-registry/) (1,000+ real agreements) via automated weekly sync.

---

## Circuit Breaker

Each source has an independent circuit breaker with 3 states:

- **CLOSED** — source is healthy, requests go through
- **OPEN** — source failed, skip for 60–300 seconds depending on failure type
- **HALF** — testing recovery after cooldown

If 2 of 3 sources timeout, the tool returns whatever it has with `partial_result: true`. It never crashes.

---

## Performance

| Scenario                      | Response Time |
| ----------------------------- | ------------- |
| Cache HIT                     | 4–6ms         |
| Cache MISS (all 3 sources)    | 1,300–4,500ms |
| Cache MISS (1 source timeout) | 1,300–2,000ms |

Cache TTL by OA status:

- `gold` / `green` → 30 days
- `hybrid` / `bronze` / `closed` → 7 days

---

## Local Setup

```bash
# Clone
git clone https://github.com/Unique-Gift/PaperPath.git
cd PaperPath

# Install dependencies
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
echo "UNPAYWALL_EMAIL=your@email.com" > .env

# Run
uvicorn main:app --host 0.0.0.0 --port 4010
```

Test:

```bash
# Health check
curl https://localhost:4010/health

# Initialize session
curl -X POST http://localhost:4010/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -D - \
  -d '{"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}}' \
  2>/dev/null | grep mcp-session-id

# Look up a paper
curl -X POST http://localhost:4010/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: YOUR_SESSION_ID" \
  -d '{"jsonrpc": "2.0", "method": "tools/call", "id": 2, "params": {"name": "find_paper_access", "arguments": {"doi": "10.1038/s41586-021-03819-2"}}}'\
  | grep "^data:" | sed 's/^data: //' | jq '.result.structuredContent'
```

---

## Deployment

Deployed on [Railway](https://railway.app) via FastMCP + uvicorn.

Required environment variable:

```
UNPAYWALL_EMAIL=your@email.com
```

---

## Roadmap

- **V2:** Ingest full ESAC registry (1,000+ institutional agreements)
- **V2:** Add CORE (core.ac.uk) as Source 4 — 200M+ open access papers
- **V2:** Weekly automated sync for institutional access data
- **V2:** Author email lookup via ORCID API

---

## License

MIT
