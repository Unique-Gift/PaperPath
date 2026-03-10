-- ============================================================
-- PaperPath: Institutional Access Detection Schema
-- ============================================================
-- Core question this schema answers:
-- "Does institution X have free access to publisher/journal Y?"
-- ============================================================


-- ------------------------------------------------------------
-- 1. INSTITUTIONS
-- Represents any university, library, or research body
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS institutions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,                  -- "Massachusetts Institute of Technology"
    short_name          TEXT,                           -- "MIT"
    domain              TEXT UNIQUE NOT NULL,           -- "mit.edu" — used for detection
    country_code        TEXT NOT NULL,                  -- "US" (ISO 3166-1 alpha-2)
    type                TEXT NOT NULL                   -- "university" | "library" | "research_institute" | "hospital"
        CHECK (type IN ('university', 'library', 'research_institute', 'hospital')),
    ror_id              TEXT UNIQUE,                    -- Research Organization Registry ID (e.g. "https://ror.org/042nb2s44")
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_institutions_domain      ON institutions(domain);
CREATE INDEX IF NOT EXISTS idx_institutions_country     ON institutions(country_code);


-- ------------------------------------------------------------
-- 2. PUBLISHERS
-- Top-level publishing entities (Elsevier, Springer, etc.)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS publishers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,                  -- "Elsevier"
    base_url            TEXT,                           -- "https://www.sciencedirect.com"
    issn_prefix         TEXT,                           -- Used to match journals to publisher
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);


-- ------------------------------------------------------------
-- 3. JOURNALS
-- Individual journals within a publisher
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS journals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    publisher_id        INTEGER NOT NULL REFERENCES publishers(id),
    name                TEXT NOT NULL,                  -- "The Lancet"
    issn_print          TEXT,                           -- "0140-6736"
    issn_online         TEXT,                           -- "1474-547X"
    subject_area        TEXT,                           -- "Medicine" | "Physics" | etc.
    is_open_access      BOOLEAN DEFAULT FALSE,          -- Fully OA journal (no paywall at all)
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_journals_issn_print      ON journals(issn_print);
CREATE INDEX IF NOT EXISTS idx_journals_issn_online     ON journals(issn_online);
CREATE INDEX IF NOT EXISTS idx_journals_publisher       ON journals(publisher_id);


-- ------------------------------------------------------------
-- 4. ACCESS AGREEMENTS
-- The core table — which institution has access to what
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS access_agreements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    institution_id      INTEGER NOT NULL REFERENCES institutions(id),
    publisher_id        INTEGER REFERENCES publishers(id),     -- Broad publisher-level deal
    journal_id          INTEGER REFERENCES journals(id),       -- OR specific journal-level deal
    agreement_type      TEXT NOT NULL                          -- Type of access granted
        CHECK (agreement_type IN (
            'full_subscription',    -- Full access to all content
            'read_and_publish',     -- Hybrid OA deal (common in EU)
            'interlibrary_loan',    -- Can request via ILL
            'open_access',          -- Fully open, no agreement needed
            'embargo_access'        -- Access after embargo period
        )),
    access_start        DATE,                                  -- When access begins
    access_end          DATE,                                  -- When access expires (NULL = ongoing)
    embargo_months      INTEGER DEFAULT 0,                     -- Months delay for embargo_access
    notes               TEXT,                                  -- e.g. "Covers 1995–present only"
    verified_at         DATETIME,                              -- Last time this was confirmed accurate
    source_url          TEXT,                                  -- Where this agreement was sourced from
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,

    -- Must link to either a publisher OR a journal, not neither
    CHECK (publisher_id IS NOT NULL OR journal_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_access_institution       ON access_agreements(institution_id);
CREATE INDEX IF NOT EXISTS idx_access_publisher         ON access_agreements(publisher_id);
CREATE INDEX IF NOT EXISTS idx_access_journal           ON access_agreements(journal_id);
CREATE INDEX IF NOT EXISTS idx_access_end               ON access_agreements(access_end);


-- ------------------------------------------------------------
-- 5. PAPERS
-- Cache of papers we've already looked up
-- Avoids re-hitting upstream APIs for the same DOI
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS papers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    doi                 TEXT UNIQUE NOT NULL,            -- "10.1038/nature14299"
    title               TEXT,
    journal_id          INTEGER REFERENCES journals(id),
    publisher_id        INTEGER REFERENCES publishers(id),
    published_date      DATE,
    is_open_access      BOOLEAN DEFAULT FALSE,
    oa_status           TEXT                             -- "gold" | "green" | "hybrid" | "bronze" | "closed"
        CHECK (oa_status IN ('gold', 'green', 'hybrid', 'bronze', 'closed', NULL)),
    cached_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    cache_expires_at    DATETIME                         -- Refresh after this date
);

CREATE INDEX IF NOT EXISTS idx_papers_doi               ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_journal           ON papers(journal_id);


-- ------------------------------------------------------------
-- 6. FREE ACCESS SOURCES
-- All known free/legal versions of a specific paper
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS free_access_sources (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id            INTEGER NOT NULL REFERENCES papers(id),
    source_name         TEXT NOT NULL,                   -- "Unpaywall" | "PubMed Central" | "CORE" etc.
    source_url          TEXT NOT NULL,                   -- Direct link to free version
    version_type        TEXT NOT NULL                    -- What version this is
        CHECK (version_type IN (
            'published',            -- Final published version (best)
            'author_accepted',      -- Post peer-review, pre-formatting
            'preprint',             -- Pre peer-review
            'submitted'             -- Original submitted draft
        )),
    is_legal            BOOLEAN DEFAULT TRUE,
    discovered_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_free_sources_paper       ON free_access_sources(paper_id);


-- ------------------------------------------------------------
-- 7. AUTHORS
-- For the author contact fallback feature
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS authors (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    email               TEXT,                            -- Institutional email if available
    institution_id      INTEGER REFERENCES institutions(id),
    orcid               TEXT UNIQUE,                     -- "0000-0002-1825-0097"
    openalex_id         TEXT UNIQUE                      -- OpenAlex author ID
);

CREATE INDEX IF NOT EXISTS idx_authors_orcid            ON authors(orcid);


-- ------------------------------------------------------------
-- 8. PAPER AUTHORS (junction table)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_authors (
    paper_id            INTEGER NOT NULL REFERENCES papers(id),
    author_id           INTEGER NOT NULL REFERENCES authors(id),
    is_corresponding    BOOLEAN DEFAULT FALSE,           -- Prioritise for contact fallback
    author_position     INTEGER,                         -- 1 = first author, etc.
    PRIMARY KEY (paper_id, author_id)
);


-- ------------------------------------------------------------
-- 9. API CALL LOGS
-- Tracks upstream API usage for debugging + rate limit mgmt
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_call_logs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name         TEXT NOT NULL,                   -- "unpaywall" | "openalex" | "core" etc.
    query               TEXT NOT NULL,                   -- The DOI or query sent
    status_code         INTEGER,                         -- HTTP response code
    response_time_ms    INTEGER,                         -- How long it took
    success             BOOLEAN NOT NULL,
    error_message       TEXT,                            -- NULL if success
    called_at           DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_api_logs_source          ON api_call_logs(source_name);
CREATE INDEX IF NOT EXISTS idx_api_logs_called_at       ON api_call_logs(called_at);


-- ============================================================
-- SAMPLE DATA — for testing institutional access detection
-- ============================================================

INSERT INTO publishers (name, base_url) VALUES
    ('Elsevier',        'https://www.sciencedirect.com'),
    ('Springer Nature', 'https://link.springer.com'),
    ('Wiley',           'https://onlinelibrary.wiley.com'),
    ('IEEE',            'https://ieeexplore.ieee.org'),
    ('ACM',             'https://dl.acm.org');

INSERT INTO institutions (name, short_name, domain, country_code, type, ror_id) VALUES
    ('Massachusetts Institute of Technology', 'MIT',      'mit.edu',       'US', 'university', 'https://ror.org/042nb2s44'),
    ('University of Oxford',                  'Oxford',   'ox.ac.uk',      'GB', 'university', 'https://ror.org/052gg0110'),
    ('University of Lagos',                   'UNILAG',   'unilag.edu.ng', 'NG', 'university', 'https://ror.org/03map3628'),
    ('Harvard University',                    'Harvard',  'harvard.edu',   'US', 'university', 'https://ror.org/03vek6s52');

INSERT INTO access_agreements (institution_id, publisher_id, agreement_type, access_start, access_end, source_url) VALUES
    (1, 1, 'read_and_publish',  '2024-01-01', NULL,         'https://libraries.mit.edu/scholarly/publishing/elsevier/'),
    (1, 2, 'full_subscription', '2023-01-01', '2026-12-31', 'https://libraries.mit.edu'),
    (2, 1, 'read_and_publish',  '2024-01-01', NULL,         'https://ox.ac.uk/libraries'),
    (4, 1, 'full_subscription', '2024-01-01', '2026-12-31', 'https://library.harvard.edu');
