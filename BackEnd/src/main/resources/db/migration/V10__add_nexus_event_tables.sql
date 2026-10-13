-- NEXUS (2026 뉴스빅데이터 해커톤): BigKinds news events with reported vs inferred company impacts.
-- Reported (basis = REPORTED) and inferred (basis = INFERRED) impacts are separated at storage time.

ALTER TABLE company_relationship
    ADD COLUMN relation_source VARCHAR(20) NOT NULL DEFAULT 'COSMOS',
    ADD CONSTRAINT chk_company_relationship_source
        CHECK (relation_source IN ('COSMOS', 'NEWS', 'DART', 'IO_TABLE'));

CREATE TABLE news_event (
    event_id               UUID PRIMARY KEY,
    document_id            UUID NOT NULL REFERENCES source_document (document_id) ON DELETE CASCADE,
    bigkinds_news_id       VARCHAR(64) NOT NULL UNIQUE,
    value_chain            VARCHAR(20) NOT NULL CHECK (value_chain IN ('SEMICONDUCTOR', 'BATTERY', 'AUTO')),
    categories             TEXT[] NOT NULL DEFAULT '{}',
    incidents              TEXT[] NOT NULL DEFAULT '{}',
    event_type             VARCHAR(50),
    event_type_confidence  NUMERIC(7, 6),
    dateline               TIMESTAMPTZ NOT NULL,
    event_trading_date     DATE,
    issue_cluster_id       VARCHAR(100),
    status                 VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CANCELLED')),
    pipeline_version       VARCHAR(50) NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_news_event_chain_dateline ON news_event (value_chain, dateline DESC);

CREATE TABLE event_company_impact (
    event_id           UUID NOT NULL REFERENCES news_event (event_id) ON DELETE CASCADE,
    company_id         UUID NOT NULL REFERENCES company (company_id),
    basis              VARCHAR(10) NOT NULL CHECK (basis IN ('REPORTED', 'INFERRED')),
    hop                SMALLINT NOT NULL CHECK (hop BETWEEN 0 AND 2),
    path               JSONB NOT NULL DEFAULT '[]',
    propagation_score  NUMERIC(7, 6) NOT NULL,
    impact_direction   VARCHAR(10) CHECK (impact_direction IN ('POSITIVE', 'NEUTRAL', 'NEGATIVE')),
    impact_magnitude   NUMERIC(7, 6),
    confidence         NUMERIC(7, 6),
    status             VARCHAR(10) NOT NULL CHECK (status IN ('OK', 'HOLD')),
    hold_reason        TEXT,
    model_version      VARCHAR(50) NOT NULL,
    PRIMARY KEY (event_id, company_id),
    CONSTRAINT chk_impact_basis_hop CHECK ((basis = 'REPORTED' AND hop = 0) OR (basis = 'INFERRED' AND hop > 0)),
    CONSTRAINT chk_impact_hold_reason CHECK (status = 'OK' OR hold_reason IS NOT NULL)
);
CREATE INDEX ix_event_company_impact_company ON event_company_impact (company_id, basis);

CREATE TABLE event_abnormal_return (
    event_id          UUID NOT NULL REFERENCES news_event (event_id) ON DELETE CASCADE,
    company_id        UUID NOT NULL REFERENCES company (company_id),
    window_days       SMALLINT NOT NULL CHECK (window_days IN (1, 3)),
    benchmark         VARCHAR(20) NOT NULL,
    stock_return      NUMERIC(12, 8) NOT NULL,
    benchmark_return  NUMERIC(12, 8) NOT NULL,
    abnormal_return   NUMERIC(12, 8) NOT NULL,
    PRIMARY KEY (event_id, company_id, window_days)
);

CREATE TABLE relation_evidence_snippet (
    snippet_id            UUID PRIMARY KEY,
    source_company_id     UUID NOT NULL REFERENCES company (company_id),
    target_company_id     UUID NOT NULL REFERENCES company (company_id),
    relationship_type_id  SMALLINT NOT NULL REFERENCES relationship_type (relationship_type_id),
    bigkinds_news_id      VARCHAR(64) NOT NULL,
    title                 TEXT NOT NULL,
    provider              VARCHAR(200),
    published_at          TIMESTAMPTZ NOT NULL,
    original_url          TEXT NOT NULL DEFAULT '',
    snippet               VARCHAR(300) NOT NULL,
    pattern               VARCHAR(30) NOT NULL,
    confidence            NUMERIC(7, 6) NOT NULL,
    CONSTRAINT uk_relation_evidence_snippet
        UNIQUE (bigkinds_news_id, source_company_id, target_company_id, relationship_type_id)
);
CREATE INDEX ix_relation_evidence_pair
    ON relation_evidence_snippet (source_company_id, target_company_id, relationship_type_id);
