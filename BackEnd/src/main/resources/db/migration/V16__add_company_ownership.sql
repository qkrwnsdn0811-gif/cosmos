-- NEXUS 기업 탭 (2026-10-26): OpenDART 타법인 출자 현황에서 가져온 상장사 사이 지분 관계.
-- nexus ownership 이 통째로 다시 쓰고, nexus publish-graph 가 은하 스냅샷의 투자(INVEST) 관계선으로 넣는다.
CREATE TABLE company_ownership (
    investor_company_id UUID NOT NULL REFERENCES company (company_id) ON DELETE CASCADE,
    investee_company_id UUID NOT NULL REFERENCES company (company_id) ON DELETE CASCADE,
    stake_pct           NUMERIC(7, 4) NOT NULL,
    purpose             VARCHAR(50),
    settled_on          DATE,
    receipt_no          VARCHAR(20),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (investor_company_id, investee_company_id),
    CONSTRAINT chk_company_ownership_stake CHECK (stake_pct > 0 AND stake_pct <= 100),
    CONSTRAINT chk_company_ownership_not_self CHECK (investor_company_id <> investee_company_id)
);
