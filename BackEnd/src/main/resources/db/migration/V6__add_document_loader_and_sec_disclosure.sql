-- Track source collection time so late historical batches cannot replace newer content.
ALTER TABLE source_document ADD COLUMN last_collected_at TIMESTAMPTZ;
UPDATE source_document SET last_collected_at = first_collected_at;
ALTER TABLE source_document
    ALTER COLUMN last_collected_at SET DEFAULT CURRENT_TIMESTAMP,
    ALTER COLUMN last_collected_at SET NOT NULL;

COMMENT ON COLUMN source_document.last_collected_at
    IS 'Newest source collection timestamp accepted by the document Loader; prevents stale snapshot overwrite';

-- Preserve existing DART rows and inserts while representing SEC identities explicitly.
ALTER TABLE disclosure
    ADD COLUMN filing_system VARCHAR(10) NOT NULL DEFAULT 'DART',
    ADD COLUMN sec_accession_no VARCHAR(20),
    ADD COLUMN sec_cik VARCHAR(10),
    ALTER COLUMN dart_receipt_no DROP NOT NULL;

ALTER TABLE disclosure
    ADD CONSTRAINT uk_disclosure_sec_cik_accession UNIQUE (sec_cik, sec_accession_no),
    ADD CONSTRAINT chk_disclosure_filing_identity CHECK (
        (filing_system = 'DART'
            AND dart_receipt_no IS NOT NULL AND length(trim(dart_receipt_no)) > 0
            AND sec_accession_no IS NULL AND sec_cik IS NULL)
        OR
        (filing_system = 'SEC'
            AND dart_receipt_no IS NULL
            AND sec_accession_no IS NOT NULL
            AND sec_accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'
            AND sec_cik IS NOT NULL AND sec_cik ~ '^[0-9]{10}$')
    );

-- One committed receipt per immutable input snapshot. A dry run leaves no receipt.
CREATE TABLE document_load_batch (
    batch_id              TEXT PRIMARY KEY,
    manifest_sha256       VARCHAR(64) NOT NULL,
    normalization_sha256  VARCHAR(64) NOT NULL,
    source_uri            TEXT NOT NULL,
    record_count          BIGINT NOT NULL,
    document_count        BIGINT NOT NULL,
    loaded_at             TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_document_load_batch_id CHECK (length(trim(batch_id)) > 0),
    CONSTRAINT chk_document_load_batch_manifest_hash
        CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_document_load_batch_normalization_hash
        CHECK (normalization_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_document_load_batch_counts
        CHECK (record_count >= 0 AND document_count >= 0 AND document_count <= record_count)
);

COMMENT ON COLUMN disclosure.filing_system IS 'DART or SEC; legacy DART inserts keep the DART default';
COMMENT ON COLUMN disclosure.sec_accession_no IS 'SEC EDGAR accession number; identity is (sec_cik, sec_accession_no), never a DART receipt';
COMMENT ON COLUMN disclosure.sec_cik IS 'SEC issuer CIK, zero-padded to ten digits';
COMMENT ON TABLE document_load_batch IS 'Document Loader checkpoint committed atomically with the complete input snapshot';
COMMENT ON COLUMN document_load_batch.normalization_sha256
    IS 'Canonical normalized records and explicit source configuration hash for exact replay verification';
