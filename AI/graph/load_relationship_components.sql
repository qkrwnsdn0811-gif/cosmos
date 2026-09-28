-- rel-v0.3-components only. Run from the generated CSV directory:
-- psql "$DATABASE_URL" -f load_relationships.sql
-- Requires BE's NEW migration adding nullable numeric news_score/disclosure_score
-- to BOTH current and history. This loader never alters the application schema.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL TIME ZONE 'UTC';

DO $$
BEGIN
  IF (SELECT count(*) FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name IN ('relationship_score_current', 'relationship_score_history')
        AND column_name IN ('news_score', 'disclosure_score')
        AND data_type = 'numeric' AND is_nullable = 'YES') <> 4 THEN
    RAISE EXCEPTION 'Apply the backend component-score migration before loading';
  END IF;
END $$;

-- Serialize complete snapshot publications, including an empty extraction result.
LOCK TABLE graph_snapshot, relationship_score_current, relationship_score_history
  IN SHARE ROW EXCLUSIVE MODE;
CREATE TEMP TABLE _rel_type (code text, name text, directionality text) ON COMMIT DROP;
CREATE TEMP TABLE _snapshot (as_of_at timestamptz NOT NULL, formula_version text NOT NULL,
  model_version text, status text, hdfs_uri text, published_at timestamptz) ON COMMIT DROP;
CREATE TEMP TABLE _rel (source_market text, source_stock_code text, target_market text,
  target_stock_code text, relationship_type_code text,
  PRIMARY KEY(source_market,source_stock_code,target_market,target_stock_code,relationship_type_code)) ON COMMIT DROP;
CREATE TEMP TABLE _score (source_market text, source_stock_code text, target_market text,
  target_stock_code text, relationship_type_code text, window_type text NOT NULL,
  news_score numeric, disclosure_score numeric, score numeric NOT NULL,
  impact_direction text, confidence numeric, evidence_count int NOT NULL,
  formula_version text NOT NULL, as_of_at timestamptz NOT NULL,
  PRIMARY KEY(source_market,source_stock_code,target_market,target_stock_code,relationship_type_code,window_type),
  CHECK(window_type IN ('7D','30D','90D')),
  CHECK(news_score IS NOT NULL OR disclosure_score IS NOT NULL),
  CHECK(news_score IS NULL OR news_score BETWEEN 0 AND 100),
  CHECK(disclosure_score IS NULL OR disclosure_score BETWEEN 0 AND 100),
  CHECK(score BETWEEN 0 AND 100),
  CHECK(confidence BETWEEN 0 AND 1), CHECK(evidence_count > 0),
  CHECK(abs(score - CASE WHEN news_score IS NULL THEN disclosure_score
    WHEN disclosure_score IS NULL THEN news_score ELSE (news_score+disclosure_score)/2 END) <= 0.000001)
) ON COMMIT DROP;

\copy _rel_type FROM 'relationship_type.csv' WITH (FORMAT csv, HEADER true)
\copy _snapshot FROM 'graph_snapshot.csv' WITH (FORMAT csv, HEADER true)
\copy _rel FROM 'company_relationship.csv' WITH (FORMAT csv, HEADER true)
\copy _score FROM 'relationship_score_current.csv' WITH (FORMAT csv, HEADER true, NULL '')

DO $$
BEGIN
  IF (SELECT count(*) FROM _snapshot) <> 1 OR EXISTS (
    SELECT 1 FROM _snapshot WHERE formula_version <> 'rel-v0.3-components' OR status <> 'PUBLISHED'
  ) THEN RAISE EXCEPTION 'Expected one complete component snapshot'; END IF;
  IF EXISTS (SELECT 1 FROM _score s WHERE NOT EXISTS (
    SELECT 1 FROM _snapshot g WHERE g.as_of_at=s.as_of_at AND g.formula_version=s.formula_version
  )) THEN RAISE EXCEPTION 'Score/snapshot mismatch'; END IF;
  IF EXISTS (SELECT 1 FROM relationship_score_current
    WHERE as_of_at > (SELECT as_of_at FROM _snapshot)) THEN
    RAISE EXCEPTION 'Refusing to replace current data with an older snapshot';
  END IF;
  IF EXISTS (SELECT 1 FROM _rel r
    LEFT JOIN company sc ON sc.market=r.source_market AND sc.stock_code=r.source_stock_code
    LEFT JOIN company tc ON tc.market=r.target_market AND tc.stock_code=r.target_stock_code
    WHERE sc.company_id IS NULL OR tc.company_id IS NULL
  ) THEN RAISE EXCEPTION 'Unmapped company: load the company dictionary first'; END IF;
  IF EXISTS (SELECT 1 FROM _score s WHERE NOT EXISTS (
    SELECT 1 FROM _rel r WHERE (r.source_market,r.source_stock_code,r.target_market,r.target_stock_code,r.relationship_type_code)
      = (s.source_market,s.source_stock_code,s.target_market,s.target_stock_code,s.relationship_type_code)
  )) THEN RAISE EXCEPTION 'Score has no relationship'; END IF;
END $$;

INSERT INTO relationship_type (code,name,directionality)
SELECT code,name,directionality FROM _rel_type
ON CONFLICT(code) DO UPDATE SET name=EXCLUDED.name,directionality=EXCLUDED.directionality;

INSERT INTO graph_snapshot(as_of_at,formula_version,model_version,status,hdfs_uri,published_at)
SELECT s.* FROM _snapshot s WHERE NOT EXISTS (
  SELECT 1 FROM graph_snapshot g WHERE g.as_of_at=s.as_of_at AND g.formula_version=s.formula_version);
DO $$ BEGIN
  IF (SELECT count(*) FROM graph_snapshot g JOIN _snapshot s
    ON g.as_of_at=s.as_of_at AND g.formula_version=s.formula_version) <> 1 THEN
    RAISE EXCEPTION 'Ambiguous snapshot identity';
  END IF;
END $$;

INSERT INTO company_relationship(source_company_id,target_company_id,relationship_type_id)
SELECT sc.company_id,tc.company_id,rt.relationship_type_id FROM _rel r
JOIN company sc ON sc.market=r.source_market AND sc.stock_code=r.source_stock_code
JOIN company tc ON tc.market=r.target_market AND tc.stock_code=r.target_stock_code
JOIN relationship_type rt ON rt.code=r.relationship_type_code
ON CONFLICT(source_company_id,target_company_id,relationship_type_id) DO NOTHING;

CREATE TEMP TABLE _resolved ON COMMIT DROP AS
SELECT cr.relationship_id,g.snapshot_id,s.* FROM _score s
JOIN company sc ON sc.market=s.source_market AND sc.stock_code=s.source_stock_code
JOIN company tc ON tc.market=s.target_market AND tc.stock_code=s.target_stock_code
JOIN relationship_type rt ON rt.code=s.relationship_type_code
JOIN company_relationship cr ON cr.source_company_id=sc.company_id AND cr.target_company_id=tc.company_id
  AND cr.relationship_type_id=rt.relationship_type_id
JOIN graph_snapshot g ON g.as_of_at=s.as_of_at AND g.formula_version=s.formula_version;
DO $$ BEGIN
  IF (SELECT count(*) FROM _resolved) <> (SELECT count(*) FROM _score) THEN
    RAISE EXCEPTION 'Natural-key mapping lost or duplicated score rows';
  END IF;
END $$;

INSERT INTO relationship_score_history(relationship_id,snapshot_id,window_type,news_score,
  disclosure_score,score,impact_direction,confidence,period_start,period_end,formula_version)
SELECT relationship_id,snapshot_id,window_type,news_score,disclosure_score,score,
  NULLIF(impact_direction,''),confidence,
  as_of_at - CASE window_type WHEN '7D' THEN interval '7 days'
    WHEN '30D' THEN interval '30 days' ELSE interval '90 days' END, as_of_at,formula_version
FROM _resolved
ON CONFLICT(relationship_id,snapshot_id,window_type) DO UPDATE SET
  news_score=EXCLUDED.news_score,disclosure_score=EXCLUDED.disclosure_score,score=EXCLUDED.score,
  impact_direction=EXCLUDED.impact_direction,confidence=EXCLUDED.confidence,
  period_start=EXCLUDED.period_start,period_end=EXCLUDED.period_end,formula_version=EXCLUDED.formula_version;

INSERT INTO relationship_score_current(relationship_id,window_type,snapshot_id,news_score,
  disclosure_score,score,impact_direction,confidence,evidence_count,formula_version,as_of_at)
SELECT relationship_id,window_type,snapshot_id,news_score,disclosure_score,score,
  NULLIF(impact_direction,''),confidence,evidence_count,formula_version,as_of_at FROM _resolved
ON CONFLICT(relationship_id,window_type) DO UPDATE SET
  snapshot_id=EXCLUDED.snapshot_id,news_score=EXCLUDED.news_score,
  disclosure_score=EXCLUDED.disclosure_score,score=EXCLUDED.score,
  impact_direction=EXCLUDED.impact_direction,confidence=EXCLUDED.confidence,
  evidence_count=EXCLUDED.evidence_count,formula_version=EXCLUDED.formula_version,as_of_at=EXCLUDED.as_of_at;

-- Complete publication: remove expired/missing current rows in these three windows.
-- Preserve other historical snapshots. A re-run corrects only this snapshot's history.
DELETE FROM relationship_score_current c WHERE c.window_type IN ('7D','30D','90D')
  AND NOT EXISTS (SELECT 1 FROM _resolved r
    WHERE r.relationship_id=c.relationship_id AND r.window_type=c.window_type);
DELETE FROM relationship_score_history h USING graph_snapshot g,_snapshot s
WHERE h.snapshot_id=g.snapshot_id AND g.as_of_at=s.as_of_at AND g.formula_version=s.formula_version
  AND h.window_type IN ('7D','30D','90D') AND NOT EXISTS (
    SELECT 1 FROM _resolved r WHERE r.relationship_id=h.relationship_id AND r.window_type=h.window_type);
UPDATE graph_snapshot g SET status=s.status,model_version=s.model_version,hdfs_uri=s.hdfs_uri,
  published_at=s.published_at FROM _snapshot s
WHERE g.as_of_at=s.as_of_at AND g.formula_version=s.formula_version;
COMMIT;
