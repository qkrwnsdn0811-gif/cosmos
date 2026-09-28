-- Relationship scores and evidence remain pinned to active_run, but Loader-owned
-- document metadata is already transactional and continuously updated in public.
-- Serving these four projections from public makes a successful Loader commit
-- visible without duplicating the full graph snapshot for every news batch.
-- Keep the validated active-run rendering for existing identities and append
-- Loader rows that the selected snapshot does not yet contain.
CREATE OR REPLACE VIEW cosmos_analysis.current_source_document AS
SELECT version_row.document_id, version_row.source_id, version_row.document_type,
       version_row.title, version_row.summary, version_row.original_url,
       version_row.published_at, version_row.first_collected_at,
       version_row.content_hash, version_row.hdfs_raw_uri,
       version_row.hdfs_clean_uri, version_row.status,
       version_row.analysis_version, version_row.created_at,
       version_row.updated_at, version_row.last_collected_at
FROM cosmos_analysis.source_document version_row
JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id
UNION ALL
SELECT live.document_id, live.source_id, live.document_type, live.title,
       live.summary, live.original_url, live.published_at,
       live.first_collected_at, live.content_hash, live.hdfs_raw_uri,
       live.hdfs_clean_uri, live.status, live.analysis_version,
       live.created_at, live.updated_at, live.last_collected_at
FROM public.source_document live
WHERE NOT EXISTS (
    SELECT 1 FROM cosmos_analysis.source_document version_row
    JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id
    WHERE version_row.document_id = live.document_id
);

CREATE OR REPLACE VIEW cosmos_analysis.current_news_article AS
SELECT version_row.document_id, version_row.publisher,
       version_row.canonical_url, version_row.canonical_url_hash,
       version_row.author
FROM cosmos_analysis.news_article version_row
JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id
UNION ALL
SELECT live.document_id, live.publisher, live.canonical_url,
       live.canonical_url_hash, live.author
FROM public.news_article live
WHERE NOT EXISTS (
    SELECT 1 FROM cosmos_analysis.news_article version_row
    JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id
    WHERE version_row.document_id = live.document_id
);

CREATE OR REPLACE VIEW cosmos_analysis.current_disclosure AS
SELECT version_row.document_id, version_row.filing_company_id,
       version_row.dart_receipt_no, version_row.report_code,
       version_row.report_name, version_row.filing_date,
       version_row.disclosure_type, version_row.correction_status,
       version_row.filing_system, version_row.sec_accession_no,
       version_row.sec_cik
FROM cosmos_analysis.disclosure version_row
JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id
UNION ALL
SELECT live.document_id, live.filing_company_id, live.dart_receipt_no,
       live.report_code, live.report_name, live.filing_date,
       live.disclosure_type, live.correction_status, live.filing_system,
       live.sec_accession_no, live.sec_cik
FROM public.disclosure live
WHERE NOT EXISTS (
    SELECT 1 FROM cosmos_analysis.disclosure version_row
    JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id
    WHERE version_row.document_id = live.document_id
);

CREATE OR REPLACE VIEW cosmos_analysis.current_company_document AS
SELECT version_row.document_id, version_row.company_id,
       version_row.mention_type, version_row.relevance_score,
       version_row.sentiment, version_row.impact_score,
       version_row.confidence, version_row.model_version,
       version_row.is_service_visible, version_row.analyzed_at
FROM cosmos_analysis.company_document version_row
JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id
UNION ALL
SELECT live.document_id, live.company_id, live.mention_type,
       live.relevance_score, live.sentiment, live.impact_score,
       live.confidence, live.model_version, live.is_service_visible,
       live.analyzed_at
FROM public.company_document live
WHERE NOT EXISTS (
    SELECT 1 FROM cosmos_analysis.company_document version_row
    JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id
    WHERE version_row.document_id = live.document_id
      AND version_row.company_id = live.company_id
);

COMMENT ON VIEW cosmos_analysis.current_source_document
    IS 'Live Loader documents plus active-run-only history; graph/evidence remain active-run versioned';
COMMENT ON VIEW cosmos_analysis.current_news_article
    IS 'Live Loader-owned news detail projection';
COMMENT ON VIEW cosmos_analysis.current_disclosure
    IS 'Live Loader-owned disclosure detail projection';
COMMENT ON VIEW cosmos_analysis.current_company_document
    IS 'Live Loader-owned document-company projection, including completed AI analysis';
