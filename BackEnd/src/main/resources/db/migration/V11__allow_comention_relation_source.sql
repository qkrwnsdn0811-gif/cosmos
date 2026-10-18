-- NEXUS 빅카인즈 은하 (2026-10-18): 같은 문장 동시언급 관계는 relation_source = 'COMENTION'으로 저장한다.
-- 'NEWS'와 나눠야 nexus load가 빅카인즈 근거 관계를 정리할 때 동시언급 관계까지 지우지 않는다.

ALTER TABLE company_relationship DROP CONSTRAINT chk_company_relationship_source;
ALTER TABLE company_relationship
    ADD CONSTRAINT chk_company_relationship_source
        CHECK (relation_source IN ('COSMOS', 'NEWS', 'DART', 'IO_TABLE', 'COMENTION'));
