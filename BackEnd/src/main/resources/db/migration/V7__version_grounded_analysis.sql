-- Keep each verified analysis run separate from live collection tables.
-- The loader captures legacy data, validates each complete version, and switches
-- active_run in one transaction. This migration does not copy or change public
-- data, choose an active run, or touch users, scraps, and collection writers.
CREATE SCHEMA IF NOT EXISTS cosmos_analysis;

CREATE TABLE cosmos_analysis.runs (
    run_id               TEXT PRIMARY KEY,
    snapshot_id          UUID,
    as_of_at             TIMESTAMPTZ NOT NULL,
    publication_sha256   TEXT NOT NULL,
    status               TEXT NOT NULL,
    counts               JSONB NOT NULL DEFAULT '{}'::JSONB,
    metadata             JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at         TIMESTAMPTZ,
    supersedes_run_id    TEXT REFERENCES cosmos_analysis.runs (run_id),
    CONSTRAINT runs_run_id_nonempty CHECK (length(btrim(run_id)) > 0),
    CONSTRAINT runs_publication_sha256_valid
        CHECK (publication_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runs_status_nonempty CHECK (length(btrim(status)) > 0),
    CONSTRAINT runs_counts_object CHECK (jsonb_typeof(counts) = 'object'),
    CONSTRAINT runs_metadata_object CHECK (jsonb_typeof(metadata) = 'object'),
    CONSTRAINT runs_supersedes_other CHECK (supersedes_run_id IS DISTINCT FROM run_id)
);

CREATE TABLE cosmos_analysis.active_run (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    run_id TEXT NOT NULL REFERENCES cosmos_analysis.runs (run_id)
);

-- Copy column names, PostgreSQL types, and type modifiers, but no public data,
-- defaults, NOT NULL constraints, triggers, or foreign keys. Unknown analysis
-- values must remain NULL, even when the legacy collector used NOT NULL/zero.
-- Each row is instead keyed by (run_id, original primary-key columns).
DO $version_tables$
DECLARE
    analysis_table TEXT;
    primary_key_columns TEXT;
    public_column_projection TEXT;
    public_table_oid OID;
BEGIN
    FOREACH analysis_table IN ARRAY ARRAY[
        'source_document',
        'news_article',
        'disclosure',
        'company_document',
        'document_evidence',
        'company_relationship',
        'relationship_evidence',
        'relationship_score_current',
        'relationship_score_history',
        'company_metric_history',
        'graph_snapshot',
        'graph_snapshot_load'
    ] LOOP
        public_table_oid := to_regclass(format('public.%I', analysis_table));
        IF public_table_oid IS NULL THEN
            RAISE EXCEPTION 'Missing required public table: %', analysis_table;
        END IF;

        SELECT string_agg(format('%I', attribute.attname), ', ' ORDER BY key_column.ordinality)
          INTO primary_key_columns
          FROM pg_constraint original_key
          CROSS JOIN LATERAL unnest(original_key.conkey)
              WITH ORDINALITY AS key_column(attnum, ordinality)
          JOIN pg_attribute attribute
            ON attribute.attrelid = original_key.conrelid
           AND attribute.attnum = key_column.attnum
         WHERE original_key.conrelid = public_table_oid
           AND original_key.contype = 'p';
        IF primary_key_columns IS NULL THEN
            RAISE EXCEPTION 'Required public table has no primary key: %', analysis_table;
        END IF;

        EXECUTE format(
            'CREATE TABLE cosmos_analysis.%I AS '
            'SELECT NULL::TEXT AS run_id, original.* FROM public.%I original WITH NO DATA',
            analysis_table, analysis_table
        );
        EXECUTE format(
            'ALTER TABLE cosmos_analysis.%I ADD PRIMARY KEY (run_id, %s), '
            'ADD FOREIGN KEY (run_id) REFERENCES cosmos_analysis.runs (run_id)',
            analysis_table, primary_key_columns
        );

        -- Expose the original public shape only. run_id is internal, and an
        -- empty active_run means an empty projection rather than public fallback.
        SELECT string_agg(format('version_row.%I', attribute.attname), ', ' ORDER BY attribute.attnum)
          INTO public_column_projection
          FROM pg_attribute attribute
         WHERE attribute.attrelid = public_table_oid
           AND attribute.attnum > 0
           AND NOT attribute.attisdropped;
        EXECUTE format(
            'CREATE VIEW cosmos_analysis.%I AS SELECT %s '
            'FROM cosmos_analysis.%I version_row '
            'JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id',
            'current_' || analysis_table, public_column_projection, analysis_table
        );
    END LOOP;
END;
$version_tables$;

CREATE INDEX source_document_run_publication
    ON cosmos_analysis.source_document (run_id, published_at DESC, document_id);
CREATE INDEX news_article_run_url_hash
    ON cosmos_analysis.news_article (run_id, canonical_url_hash);
CREATE INDEX company_document_run_company_visible
    ON cosmos_analysis.company_document (run_id, company_id, is_service_visible, document_id);
CREATE INDEX company_document_run_document_visible
    ON cosmos_analysis.company_document (run_id, document_id, is_service_visible);
CREATE INDEX document_evidence_run_document_company
    ON cosmos_analysis.document_evidence (run_id, document_id, company_id, sentence_order);
CREATE INDEX company_relationship_run_identity
    ON cosmos_analysis.company_relationship
        (run_id, source_company_id, target_company_id, relationship_type_id);
CREATE INDEX relationship_evidence_run_relationship
    ON cosmos_analysis.relationship_evidence (run_id, relationship_id, document_id);
CREATE INDEX relationship_evidence_run_document
    ON cosmos_analysis.relationship_evidence (run_id, document_id);
CREATE INDEX relationship_score_current_run_snapshot_window
    ON cosmos_analysis.relationship_score_current (run_id, snapshot_id, window_type, score DESC);
CREATE INDEX relationship_score_history_run_relationship_window
    ON cosmos_analysis.relationship_score_history (run_id, relationship_id, window_type, period_end DESC);
CREATE INDEX company_metric_history_run_company_window_time
    ON cosmos_analysis.company_metric_history (run_id, company_id, window_type, measured_at DESC);
CREATE INDEX graph_snapshot_run_status_time
    ON cosmos_analysis.graph_snapshot (run_id, status, as_of_at DESC);

-- One representative context per canonical document. Other cited original
-- revisions remain in evidence_context. sort_at is a documented conservative
-- display/availability ordering time, not a fabricated published_at.
CREATE TABLE cosmos_analysis.document_context (
    run_id                 TEXT NOT NULL REFERENCES cosmos_analysis.runs (run_id),
    document_id            UUID NOT NULL,
    source_revision_id     TEXT NOT NULL,
    sort_at                TIMESTAMPTZ NOT NULL,
    published_at_raw       TEXT,
    publication_precision  TEXT,
    payload                JSONB NOT NULL DEFAULT '{}'::JSONB,
    PRIMARY KEY (run_id, document_id),
    FOREIGN KEY (run_id, document_id)
        REFERENCES cosmos_analysis.source_document (run_id, document_id),
    CHECK (jsonb_typeof(payload) = 'object')
);
CREATE INDEX document_context_run_sort
    ON cosmos_analysis.document_context (run_id, sort_at DESC, document_id);

CREATE VIEW cosmos_analysis.current_document_context AS
SELECT context.document_id, context.source_revision_id, context.sort_at,
       context.published_at_raw, context.publication_precision, context.payload
FROM cosmos_analysis.document_context context
JOIN cosmos_analysis.active_run selected ON selected.run_id = context.run_id;

CREATE TABLE cosmos_analysis.relationship_context (
    run_id           TEXT NOT NULL REFERENCES cosmos_analysis.runs (run_id),
    relationship_id  UUID NOT NULL,
    explanation      JSONB,
    payload          JSONB NOT NULL DEFAULT '{}'::JSONB,
    PRIMARY KEY (run_id, relationship_id),
    FOREIGN KEY (run_id, relationship_id)
        REFERENCES cosmos_analysis.company_relationship (run_id, relationship_id),
    CHECK (jsonb_typeof(payload) = 'object')
);

CREATE VIEW cosmos_analysis.current_relationship_context AS
SELECT context.relationship_id, context.explanation, context.payload
FROM cosmos_analysis.relationship_context context
JOIN cosmos_analysis.active_run selected ON selected.run_id = context.run_id;

CREATE TABLE cosmos_analysis.evidence_context (
    run_id                 TEXT NOT NULL REFERENCES cosmos_analysis.runs (run_id),
    evidence_id            UUID NOT NULL,
    source_revision_id     TEXT,
    source_kind            TEXT,
    available_at           TIMESTAMPTZ,
    published_at_raw       TEXT,
    publication_precision  TEXT,
    payload                JSONB NOT NULL DEFAULT '{}'::JSONB,
    PRIMARY KEY (run_id, evidence_id),
    FOREIGN KEY (run_id, evidence_id)
        REFERENCES cosmos_analysis.document_evidence (run_id, evidence_id),
    CHECK (jsonb_typeof(payload) = 'object')
);
CREATE INDEX evidence_context_run_available
    ON cosmos_analysis.evidence_context (run_id, available_at);

CREATE VIEW cosmos_analysis.current_evidence_context AS
SELECT context.evidence_id, context.source_revision_id, context.source_kind,
       context.available_at, context.published_at_raw,
       context.publication_precision, context.payload
FROM cosmos_analysis.evidence_context context
JOIN cosmos_analysis.active_run selected ON selected.run_id = context.run_id;

COMMENT ON SCHEMA cosmos_analysis
    IS 'Immutable validated analysis versions and one selected service projection; live collection remains in public';
COMMENT ON TABLE cosmos_analysis.active_run
    IS 'Zero or one selected version; the validated publisher changes this pointer atomically, without merging runs';
COMMENT ON COLUMN cosmos_analysis.document_context.sort_at
    IS 'Declared display/availability ordering time; published_at and original precision remain separate';
