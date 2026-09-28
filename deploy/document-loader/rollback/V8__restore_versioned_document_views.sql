CREATE OR REPLACE VIEW cosmos_analysis.current_source_document AS
SELECT version_row.document_id, version_row.source_id, version_row.document_type,
       version_row.title, version_row.summary, version_row.original_url,
       version_row.published_at, version_row.first_collected_at,
       version_row.content_hash, version_row.hdfs_raw_uri,
       version_row.hdfs_clean_uri, version_row.status,
       version_row.analysis_version, version_row.created_at,
       version_row.updated_at, version_row.last_collected_at
FROM cosmos_analysis.source_document version_row
JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id;

CREATE OR REPLACE VIEW cosmos_analysis.current_news_article AS
SELECT version_row.document_id, version_row.publisher,
       version_row.canonical_url, version_row.canonical_url_hash,
       version_row.author
FROM cosmos_analysis.news_article version_row
JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id;

CREATE OR REPLACE VIEW cosmos_analysis.current_disclosure AS
SELECT version_row.document_id, version_row.filing_company_id,
       version_row.dart_receipt_no, version_row.report_code,
       version_row.report_name, version_row.filing_date,
       version_row.disclosure_type, version_row.correction_status,
       version_row.filing_system, version_row.sec_accession_no,
       version_row.sec_cik
FROM cosmos_analysis.disclosure version_row
JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id;

CREATE OR REPLACE VIEW cosmos_analysis.current_company_document AS
SELECT version_row.document_id, version_row.company_id,
       version_row.mention_type, version_row.relevance_score,
       version_row.sentiment, version_row.impact_score,
       version_row.confidence, version_row.model_version,
       version_row.is_service_visible, version_row.analyzed_at
FROM cosmos_analysis.company_document version_row
JOIN cosmos_analysis.active_run selected ON selected.run_id = version_row.run_id;
