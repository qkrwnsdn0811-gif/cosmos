-- 성공한 HDFS 전체 집계 산출물의 적재 영수증. 스냅샷과 같은 트랜잭션에서 기록한다.
-- 기존 V1~V4와 기존 스냅샷은 변경하지 않는다. 영수증이 없는 기존 ID를 재사용하지 않는다.
CREATE TABLE graph_snapshot_load (
    snapshot_id       UUID PRIMARY KEY,
    manifest_sha256   VARCHAR(64) NOT NULL,
    record_count      BIGINT NOT NULL,
    stored_count      BIGINT NOT NULL,
    windows           TEXT[] NOT NULL,
    loaded_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_graph_snapshot_load_snapshot
        FOREIGN KEY (snapshot_id) REFERENCES graph_snapshot (snapshot_id),
    CONSTRAINT chk_graph_snapshot_load_hash
        CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_graph_snapshot_load_counts
        CHECK (record_count >= 0 AND stored_count >= 0 AND stored_count <= record_count),
    CONSTRAINT chk_graph_snapshot_load_windows
        CHECK (windows = ARRAY['7D', '30D', '90D']::TEXT[])
);

COMMENT ON TABLE graph_snapshot_load
    IS '동일 snapshot_id의 정확한 재시도를 확인하는 HDFS 집계 적재 영수증';
COMMENT ON COLUMN graph_snapshot_load.manifest_sha256
    IS '파일별 SHA-256과 스냅샷 메타데이터를 포함한 canonical manifest의 SHA-256';
COMMENT ON COLUMN graph_snapshot_load.record_count
    IS 'manifest에 선언된 전체 원본 행 수. 두 구성 점수가 모두 NULL인 행을 포함';
COMMENT ON COLUMN graph_snapshot_load.stored_count
    IS '두 구성 점수가 모두 NULL인 행을 제외하고 서비스에 게시한 점수 행 수';
