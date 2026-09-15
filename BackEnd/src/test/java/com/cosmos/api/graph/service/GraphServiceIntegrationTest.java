package com.cosmos.api.graph.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.graph.dto.CompanyGraphResponse;
import com.cosmos.api.graph.dto.LatestGraphResponse;
import com.cosmos.api.graph.dto.RelationshipDetailResponse;
import com.cosmos.api.graph.dto.RelationshipEvidenceResponse;
import com.cosmos.api.graph.error.GraphErrorCode;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

@SpringBootTest
@Transactional
class GraphServiceIntegrationTest {

    private static final UUID COMPANY_A = UUID.fromString("50000000-0000-0000-0000-000000000001");
    private static final UUID COMPANY_B = UUID.fromString("50000000-0000-0000-0000-000000000002");
    private static final UUID COMPANY_C = UUID.fromString("50000000-0000-0000-0000-000000000003");
    private static final UUID INDUSTRY_ID = UUID.fromString("50000000-0000-0000-0000-000000000010");
    private static final UUID SNAPSHOT_ID = UUID.fromString("50000000-0000-0000-0000-000000000020");
    private static final UUID RELATIONSHIP_AB = UUID.fromString("50000000-0000-0000-0000-000000000031");
    private static final UUID RELATIONSHIP_BC = UUID.fromString("50000000-0000-0000-0000-000000000032");
    private static final UUID NEWS_1 = UUID.fromString("50000000-0000-0000-0000-000000000041");
    private static final UUID NEWS_2 = UUID.fromString("50000000-0000-0000-0000-000000000042");
    private static final UUID DISCLOSURE = UUID.fromString("50000000-0000-0000-0000-000000000043");
    private static final UUID SOURCE_ID = UUID.fromString("50000000-0000-0000-0000-000000000050");

    @Autowired
    private GraphService graphService;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @BeforeEach
    void setUp() {
        jdbcTemplate.update("""
                INSERT INTO industry (industry_id, name, description)
                VALUES (?, '반도체', '테스트 산업')
                """, INDUSTRY_ID);
        insertCompany(COMPANY_A, "기업A", "A001", "KOSPI");
        insertCompany(COMPANY_B, "기업B", "B001", "NASDAQ");
        insertCompany(COMPANY_C, "기업C", "C001", "KOSPI");
        jdbcTemplate.update("INSERT INTO company_industry VALUES (?, ?, TRUE)", COMPANY_A, INDUSTRY_ID);
        jdbcTemplate.update("INSERT INTO company_industry VALUES (?, ?, TRUE)", COMPANY_B, INDUSTRY_ID);
        jdbcTemplate.update("""
                INSERT INTO relationship_type (relationship_type_id, code, name, directionality)
                VALUES (500, 'SUPPLY', '공급', 'DIRECTED')
                """);
        jdbcTemplate.update("""
                INSERT INTO graph_snapshot
                    (snapshot_id, as_of_at, formula_version, model_version, status, hdfs_uri, published_at)
                VALUES (?, '2026-09-07T06:00:00Z', 'formula-v1', 'model-v1', 'PUBLISHED',
                        'hdfs://graph/test', '2026-09-07T06:05:00Z')
                """, SNAPSHOT_ID);
        insertRelationship(RELATIONSHIP_AB, COMPANY_A, COMPANY_B, "82.4", 16);
        insertRelationship(RELATIONSHIP_BC, COMPANY_B, COMPANY_C, "60.0", 4);
        jdbcTemplate.update("""
                INSERT INTO data_source (source_id, name, source_type)
                VALUES (?, '그래프 테스트 출처', 'NEWS')
                """, SOURCE_ID);
        insertNews(NEWS_1, "최근 뉴스", "2026-09-07T02:00:00Z");
        insertNews(NEWS_2, "이전 뉴스", "2026-09-06T02:00:00Z");
        insertDisclosure(DISCLOSURE);
        insertEvidence(NEWS_1, "최근 근거 문장", "0.760000", "0.910000");
        insertAdditionalEvidence(NEWS_1, "보조 근거 문장", "0.300000", "0.700000");
        insertEvidence(NEWS_2, "이전 근거 문장", "0.650000", "0.850000");
        jdbcTemplate.update("""
                INSERT INTO relationship_evidence
                    (relationship_id, document_id, contribution_score, model_version, formula_version)
                VALUES (?, ?, 0.5, 'model-v1', 'formula-v1')
                """, RELATIONSHIP_AB, DISCLOSURE);
    }

    @Test
    void returnsLatestPublishedGraphForDefaultUniverse() {
        LatestGraphResponse result = graphService.findLatestGraph(null, null);

        assertThat(result.snapshotId()).isEqualTo(SNAPSHOT_ID);
        assertThat(result.asOfAt()).hasToString("2026-09-07T06:00:00Z");
        assertThat(result.nextRefreshAt()).hasToString("2026-09-07T07:00:00Z");
        assertThat(result.personalized()).isFalse();
        assertThat(result.nodes()).extracting(node -> node.companyId())
                .containsExactly(COMPANY_A, COMPANY_B, COMPANY_C);
        assertThat(result.edges()).hasSize(2);
        assertThat(result.edges().getFirst().newsScore()).isEqualByComparingTo("75.000000");
        assertThat(result.edges().getFirst().disclosureScore()).isEqualByComparingTo("90.000000");
    }

    @Test
    void filtersLatestGraphByIndustry() {
        LatestGraphResponse result = graphService.findLatestGraph(null, INDUSTRY_ID);

        assertThat(result.nodes()).extracting(node -> node.companyId())
                .containsExactly(COMPANY_A, COMPANY_B);
        assertThat(result.edges()).singleElement()
                .satisfies(edge -> assertThat(edge.relationshipId()).isEqualTo(RELATIONSHIP_AB));
    }

    @Test
    void returnsCompanyGraphWithBreadthFirstDepths() {
        CompanyGraphResponse result = graphService.findCompanyGraph(COMPANY_A, 3);

        assertThat(result.centerCompanyId()).isEqualTo(COMPANY_A);
        assertThat(result.nodes()).extracting(node -> node.companyId(), node -> node.depth())
                .containsExactly(
                        org.assertj.core.groups.Tuple.tuple(COMPANY_A, 0),
                        org.assertj.core.groups.Tuple.tuple(COMPANY_B, 1),
                        org.assertj.core.groups.Tuple.tuple(COMPANY_C, 2));
        assertThat(result.edges()).hasSize(2);
    }

    @Test
    void returnsRelationshipDetailForRequestedWindow() {
        RelationshipDetailResponse result = graphService.findRelationship(RELATIONSHIP_AB, "30d");

        assertThat(result.relationshipType()).isEqualTo("SUPPLY");
        assertThat(result.directionality()).isEqualTo("DIRECTED");
        assertThat(result.window()).isEqualTo("30D");
        assertThat(result.score()).isEqualByComparingTo("82.400000");
        assertThat(result.newsScore()).isEqualByComparingTo("75.000000");
        assertThat(result.disclosureScore()).isEqualByComparingTo("90.000000");
        assertThat(result.evidenceCount()).isEqualTo(16);
    }

    @Test
    void preservesMissingSourceScoreAsNull() {
        jdbcTemplate.update("""
                UPDATE relationship_score_current
                SET disclosure_score = NULL
                WHERE relationship_id = ? AND window_type = '30D'
                """, RELATIONSHIP_AB);

        RelationshipDetailResponse result = graphService.findRelationship(RELATIONSHIP_AB, "30D");

        assertThat(result.newsScore()).isEqualByComparingTo("75.000000");
        assertThat(result.disclosureScore()).isNull();
    }

    @Test
    void pagesOnlyNewsEvidenceInNewestFirstOrder() {
        CursorPageResponse<RelationshipEvidenceResponse> first =
                graphService.findRelationshipEvidence(RELATIONSHIP_AB, null, 1);

        assertThat(first.items()).singleElement()
                .satisfies(item -> {
                    assertThat(item.newsId()).isEqualTo(NEWS_1);
                    assertThat(item.evidenceSentence()).isEqualTo("최근 근거 문장");
                });
        assertThat(first.hasNext()).isTrue();
        assertThat(first.nextCursor()).isNotBlank();

        CursorPageResponse<RelationshipEvidenceResponse> second =
                graphService.findRelationshipEvidence(RELATIONSHIP_AB, first.nextCursor(), 1);
        assertThat(second.items()).extracting(RelationshipEvidenceResponse::newsId)
                .containsExactly(NEWS_2);
        assertThat(second.hasNext()).isFalse();
    }

    @Test
    void rejectsUnsupportedUniverseWindowAndCursor() {
        assertError(
                () -> graphService.findLatestGraph("KOSDAQ100", null),
                CommonErrorCode.VALIDATION_FAILED);
        assertError(
                () -> graphService.findRelationship(RELATIONSHIP_AB, "14D"),
                CommonErrorCode.VALIDATION_FAILED);
        assertError(
                () -> graphService.findRelationshipEvidence(RELATIONSHIP_AB, "invalid", 10),
                CommonErrorCode.INVALID_CURSOR);
        assertError(
                () -> graphService.findCompanyGraph(COMPANY_A, 4),
                CommonErrorCode.VALIDATION_FAILED);
        assertError(
                () -> graphService.findRelationshipEvidence(RELATIONSHIP_AB, null, 21),
                CommonErrorCode.VALIDATION_FAILED);
    }

    @Test
    void rejectsMissingRelationshipAndSnapshot() {
        UUID missing = UUID.fromString("50000000-0000-0000-0000-000000000099");
        assertError(
                () -> graphService.findRelationship(missing, "30D"),
                GraphErrorCode.RELATIONSHIP_NOT_FOUND);
        assertError(
                () -> graphService.findRelationshipEvidence(missing, null, 10),
                GraphErrorCode.RELATIONSHIP_NOT_FOUND);

        jdbcTemplate.update("UPDATE graph_snapshot SET status = 'ARCHIVED' WHERE snapshot_id = ?", SNAPSHOT_ID);
        assertError(
                () -> graphService.findLatestGraph(null, null),
                GraphErrorCode.GRAPH_SNAPSHOT_NOT_FOUND);
    }

    private void assertError(Runnable action, Object expectedCode) {
        assertThatThrownBy(action::run)
                .isInstanceOf(BusinessException.class)
                .satisfies(exception -> assertThat(((BusinessException) exception).getErrorCode())
                        .isEqualTo(expectedCode));
    }

    private void insertCompany(UUID id, String name, String stockCode, String market) {
        jdbcTemplate.update("""
                INSERT INTO company (company_id, name, stock_code, market, status)
                VALUES (?, ?, ?, ?, 'ACTIVE')
                """, id, name, stockCode, market);
    }

    private void insertRelationship(
            UUID relationshipId,
            UUID sourceCompanyId,
            UUID targetCompanyId,
            String score,
            int evidenceCount
    ) {
        jdbcTemplate.update("""
                INSERT INTO company_relationship
                    (relationship_id, source_company_id, target_company_id, relationship_type_id)
                VALUES (?, ?, ?, 500)
                """, relationshipId, sourceCompanyId, targetCompanyId);
        jdbcTemplate.update("""
                INSERT INTO relationship_score_current
                    (relationship_id, window_type, snapshot_id, score, news_score,
                     disclosure_score, impact_direction,
                     confidence, evidence_count, formula_version, as_of_at)
                VALUES (?, '30D', ?, CAST(? AS NUMERIC), 75.0, 90.0, 'POSITIVE', 0.88, ?,
                        'formula-v1', '2026-09-07T06:00:00Z')
                """, relationshipId, SNAPSHOT_ID, score, evidenceCount);
    }

    private void insertNews(UUID newsId, String title, String publishedAt) {
        jdbcTemplate.update("""
                INSERT INTO source_document
                    (document_id, source_id, document_type, title, summary, original_url,
                     published_at, status)
                VALUES (?, ?, 'NEWS', ?, '기사 요약', 'https://example.com/' || ?,
                        CAST(? AS TIMESTAMPTZ), 'ANALYZED')
                """, newsId, SOURCE_ID, title, newsId.toString(), publishedAt);
        jdbcTemplate.update("""
                INSERT INTO news_article
                    (document_id, publisher, canonical_url, canonical_url_hash)
                VALUES (?, '예시경제', 'https://example.com/' || ?, ?)
                """, newsId, newsId.toString(), "a".repeat(32) + newsId.toString().replace("-", ""));
    }

    private void insertDisclosure(UUID documentId) {
        jdbcTemplate.update("""
                INSERT INTO source_document
                    (document_id, source_id, document_type, title, original_url, published_at, status)
                VALUES (?, ?, 'DISCLOSURE', '테스트 공시', 'https://dart.example/test',
                        '2026-09-08T00:00:00Z', 'ANALYZED')
                """, documentId, SOURCE_ID);
        jdbcTemplate.update("""
                INSERT INTO disclosure
                    (document_id, filing_company_id, dart_receipt_no, report_name, filing_date)
                VALUES (?, ?, '202609080001', '테스트 보고서', '2026-09-08')
                """, documentId, COMPANY_A);
    }

    private void insertEvidence(
            UUID documentId,
            String sentence,
            String contribution,
            String confidence
    ) {
        UUID evidenceId = UUID.nameUUIDFromBytes(documentId.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
        jdbcTemplate.update("""
                INSERT INTO company_document
                    (document_id, company_id, relevance_score, confidence, is_service_visible)
                VALUES (?, ?, 0.9, 0.9, TRUE)
                """, documentId, COMPANY_A);
        jdbcTemplate.update("""
                INSERT INTO document_evidence
                    (evidence_id, document_id, company_id, sentence_text, sentence_order, confidence)
                VALUES (?, ?, ?, ?, 0, CAST(? AS NUMERIC))
                """, evidenceId, documentId, COMPANY_A, sentence, confidence);
        jdbcTemplate.update("""
                INSERT INTO relationship_evidence
                    (relationship_id, document_id, evidence_id, contribution_score,
                     model_version, formula_version)
                VALUES (?, ?, ?, CAST(? AS NUMERIC), 'model-v1', 'formula-v1')
                """, RELATIONSHIP_AB, documentId, evidenceId, contribution);
    }

    private void insertAdditionalEvidence(
            UUID documentId,
            String sentence,
            String contribution,
            String confidence
    ) {
        UUID evidenceId = UUID.nameUUIDFromBytes(
                (documentId + "-additional").getBytes(java.nio.charset.StandardCharsets.UTF_8));
        jdbcTemplate.update("""
                INSERT INTO document_evidence
                    (evidence_id, document_id, company_id, sentence_text, sentence_order, confidence)
                VALUES (?, ?, ?, ?, 1, CAST(? AS NUMERIC))
                """, evidenceId, documentId, COMPANY_A, sentence, confidence);
        jdbcTemplate.update("""
                INSERT INTO relationship_evidence
                    (relationship_id, document_id, evidence_id, contribution_score,
                     model_version, formula_version)
                VALUES (?, ?, ?, CAST(? AS NUMERIC), 'model-v1', 'formula-v1')
                """, RELATIONSHIP_AB, documentId, evidenceId, contribution);
    }
}
