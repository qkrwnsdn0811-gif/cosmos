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
    /** 뉴스 근거 문장은 기사에서 오려낸 인용문이고 공시 근거 문장은 공시 내용을 요약해 만든 생성문이다 — 화면은 이 모델 버전으로 둘을 구분한다 */
    private static final String NEWS_MODEL = "dict-v1.3+finbert-evidence-v1";
    private static final String DISCLOSURE_MODEL = "disclosure-summary-v1";

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
        insertStockPrice(COMPANY_A, "2026-06-19T00:00:00Z", "50.0");
        insertStockPrice(COMPANY_A, "2026-08-19T00:00:00Z", "80.0");
        insertStockPrice(COMPANY_A, "2026-09-11T00:00:00Z", "100.0");
        insertStockPrice(COMPANY_A, "2026-09-18T00:00:00Z", "110.0");
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
        insertRelationshipScore(RELATIONSHIP_AB, "1Y", "91.0", 28);
        insertRelationshipScore(RELATIONSHIP_AB, "10Y", "78.0", 124);
        insertRelationshipScore(RELATIONSHIP_BC, "10Y", "64.0", 37);
        jdbcTemplate.update("""
                INSERT INTO data_source (source_id, name, source_type)
                VALUES (?, '그래프 테스트 출처', 'NEWS')
                """, SOURCE_ID);
        insertNews(NEWS_1, "최근 뉴스", "2026-09-07T02:00:00Z");
        insertNews(NEWS_2, "이전 뉴스", "2026-09-06T02:00:00Z");
        insertDisclosure(DISCLOSURE);
        insertEvidence(NEWS_1, "최근 근거 문장", NEWS_MODEL, "0.760000", "0.910000");
        insertAdditionalEvidence(NEWS_1, "보조 근거 문장", NEWS_MODEL, "0.300000", "0.700000");
        insertEvidence(NEWS_2, "이전 근거 문장", NEWS_MODEL, "0.650000", "0.850000");
        // 공시도 관계 근거가 된다 — 근거 조회는 뉴스와 공시를 한 목록에 최신순으로 섞어 낸다
        insertEvidence(DISCLOSURE, "공시 요약 근거 문장", DISCLOSURE_MODEL, "0.500000", "0.800000");
    }

    @Test
    void returnsLatestPublishedGraphForDefaultUniverse() {
        LatestGraphResponse result = graphService.findLatestGraph(null, null, "30D");

        assertThat(result.snapshotId()).isEqualTo(SNAPSHOT_ID);
        assertThat(result.asOfAt()).hasToString("2026-09-07T06:00:00Z");
        assertThat(result.nextRefreshAt()).hasToString("2026-09-07T07:00:00Z");
        assertThat(result.window()).isEqualTo("30D");
        assertThat(result.personalized()).isFalse();
        assertThat(result.nodes()).extracting(node -> node.companyId())
                .containsExactly(COMPANY_A, COMPANY_B, COMPANY_C);
        assertThat(result.nodes().getFirst().priceChange()).satisfies(priceChange -> {
            assertThat(priceChange.sevenDays()).isEqualByComparingTo("10.000000");
            assertThat(priceChange.thirtyDays()).isEqualByComparingTo("37.500000");
            assertThat(priceChange.ninetyDays()).isEqualByComparingTo("120.000000");
        });
        assertThat(result.nodes().get(1).priceChange()).satisfies(priceChange -> {
            assertThat(priceChange.sevenDays()).isNull();
            assertThat(priceChange.thirtyDays()).isNull();
            assertThat(priceChange.ninetyDays()).isNull();
        });
        assertThat(result.edges()).hasSize(2);
        assertThat(result.edges().getFirst().newsScore()).isEqualByComparingTo("75.000000");
        assertThat(result.edges().getFirst().disclosureScore()).isEqualByComparingTo("90.000000");
    }

    @Test
    void filtersLatestGraphByIndustry() {
        LatestGraphResponse result = graphService.findLatestGraph(null, INDUSTRY_ID, "30D");

        assertThat(result.nodes()).extracting(node -> node.companyId())
                .containsExactly(COMPANY_A, COMPANY_B);
        assertThat(result.edges()).singleElement()
                .satisfies(edge -> assertThat(edge.relationshipId()).isEqualTo(RELATIONSHIP_AB));
    }

    @Test
    void returnsCompanyGraphWithBreadthFirstDepths() {
        CompanyGraphResponse result = graphService.findCompanyGraph(COMPANY_A, 3, "30D");

        assertThat(result.centerCompanyId()).isEqualTo(COMPANY_A);
        assertThat(result.nodes()).extracting(node -> node.companyId(), node -> node.depth())
                .containsExactly(
                        org.assertj.core.groups.Tuple.tuple(COMPANY_A, 0),
                        org.assertj.core.groups.Tuple.tuple(COMPANY_B, 1),
                        org.assertj.core.groups.Tuple.tuple(COMPANY_C, 2));
        assertThat(result.edges()).hasSize(2);
    }

    @Test
    void filtersWholeAndCompanyGraphsByRequestedWindow() {
        LatestGraphResponse latest = graphService.findLatestGraph(null, null, "1y");
        CompanyGraphResponse company = graphService.findCompanyGraph(COMPANY_A, 3, "1Y");

        assertThat(latest.window()).isEqualTo("1Y");
        assertThat(latest.edges()).singleElement()
                .satisfies(edge -> assertThat(edge.relationshipId()).isEqualTo(RELATIONSHIP_AB));
        assertThat(company.window()).isEqualTo("1Y");
        assertThat(company.nodes()).extracting(node -> node.companyId())
                .containsExactly(COMPANY_A, COMPANY_B);
        assertThat(company.edges()).singleElement()
                .satisfies(edge -> assertThat(edge.score()).isEqualByComparingTo("91.000000"));
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
    void pagesNewsAndDisclosureEvidenceTogetherInNewestFirstOrder() {
        CursorPageResponse<RelationshipEvidenceResponse> first =
                graphService.findRelationshipEvidence(RELATIONSHIP_AB, null, 1);

        // 공시(9/8)가 두 뉴스(9/7, 9/6)보다 최신이라 첫 장에 온다. 발행처 자리에는 접수 시스템(DART)이 들어가고,
        // 문장은 인용이 아닌 요약 생성문이므로 종류와 모델 버전으로 뉴스와 구분된다
        assertThat(first.items()).singleElement()
                .satisfies(item -> {
                    assertThat(item.newsId()).isEqualTo(DISCLOSURE);
                    assertThat(item.documentType()).isEqualTo("DISCLOSURE");
                    assertThat(item.publisher()).isEqualTo("DART");
                    assertThat(item.publishedAt()).hasToString("2026-09-08T00:00:00Z");
                    assertThat(item.evidenceSentence()).isEqualTo("공시 요약 근거 문장");
                    assertThat(item.modelVersion()).isEqualTo(DISCLOSURE_MODEL);
                });
        assertThat(first.hasNext()).isTrue();
        assertThat(first.nextCursor()).isNotBlank();

        // 같은 기사에 근거 문장이 둘이면 기여도가 큰 문장 하나만 대표로 나온다
        CursorPageResponse<RelationshipEvidenceResponse> second =
                graphService.findRelationshipEvidence(RELATIONSHIP_AB, first.nextCursor(), 1);
        assertThat(second.items()).singleElement()
                .satisfies(item -> {
                    assertThat(item.newsId()).isEqualTo(NEWS_1);
                    assertThat(item.documentType()).isEqualTo("NEWS");
                    assertThat(item.publisher()).isEqualTo("예시경제");
                    assertThat(item.evidenceSentence()).isEqualTo("최근 근거 문장");
                    assertThat(item.modelVersion()).isEqualTo(NEWS_MODEL);
                });
        assertThat(second.hasNext()).isTrue();

        CursorPageResponse<RelationshipEvidenceResponse> third =
                graphService.findRelationshipEvidence(RELATIONSHIP_AB, second.nextCursor(), 1);
        assertThat(third.items()).extracting(RelationshipEvidenceResponse::newsId)
                .containsExactly(NEWS_2);
        assertThat(third.hasNext()).isFalse();
    }

    @Test
    void rejectsUnsupportedUniverseWindowAndCursor() {
        assertError(
                () -> graphService.findLatestGraph("KOSDAQ100", null, "30D"),
                CommonErrorCode.VALIDATION_FAILED);
        assertError(
                () -> graphService.findLatestGraph(null, null, "14D"),
                CommonErrorCode.VALIDATION_FAILED);
        assertError(
                () -> graphService.findRelationship(RELATIONSHIP_AB, "14D"),
                CommonErrorCode.VALIDATION_FAILED);
        assertError(
                () -> graphService.findLatestGraph(null, null, "7D"),
                CommonErrorCode.VALIDATION_FAILED);
        assertError(
                () -> graphService.findRelationship(RELATIONSHIP_AB, "90D"),
                CommonErrorCode.VALIDATION_FAILED);
        assertError(
                () -> graphService.findRelationshipEvidence(RELATIONSHIP_AB, "invalid", 10),
                CommonErrorCode.INVALID_CURSOR);
        assertError(
                () -> graphService.findCompanyGraph(COMPANY_A, 4, "30D"),
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
                () -> graphService.findLatestGraph(null, null, "30D"),
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

    private void insertStockPrice(UUID companyId, String tradingAt, String closePrice) {
        jdbcTemplate.update("""
                INSERT INTO stock_price_history
                    (company_id, trading_at, interval_type, open_price, high_price,
                     low_price, close_price, trading_volume)
                VALUES (?, CAST(? AS TIMESTAMPTZ), '1D', CAST(? AS NUMERIC),
                        CAST(? AS NUMERIC), CAST(? AS NUMERIC), CAST(? AS NUMERIC), 1000)
                """, companyId, tradingAt, closePrice, closePrice, closePrice, closePrice);
    }

    private void insertRelationshipScore(
            UUID relationshipId,
            String window,
            String score,
            int evidenceCount
    ) {
        jdbcTemplate.update("""
                INSERT INTO relationship_score_current
                    (relationship_id, window_type, snapshot_id, score, news_score,
                     disclosure_score, impact_direction,
                     confidence, evidence_count, formula_version, as_of_at)
                VALUES (?, ?, ?, CAST(? AS NUMERIC), 75.0, 90.0, 'POSITIVE', 0.88, ?,
                        'formula-v1', '2026-09-07T06:00:00Z')
                """, relationshipId, window, SNAPSHOT_ID, score, evidenceCount);
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
            String modelVersion,
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
                    (evidence_id, document_id, company_id, sentence_text, sentence_order, confidence,
                     model_version)
                VALUES (?, ?, ?, ?, 0, CAST(? AS NUMERIC), ?)
                """, evidenceId, documentId, COMPANY_A, sentence, confidence, modelVersion);
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
            String modelVersion,
            String contribution,
            String confidence
    ) {
        UUID evidenceId = UUID.nameUUIDFromBytes(
                (documentId + "-additional").getBytes(java.nio.charset.StandardCharsets.UTF_8));
        jdbcTemplate.update("""
                INSERT INTO document_evidence
                    (evidence_id, document_id, company_id, sentence_text, sentence_order, confidence,
                     model_version)
                VALUES (?, ?, ?, ?, 1, CAST(? AS NUMERIC), ?)
                """, evidenceId, documentId, COMPANY_A, sentence, confidence, modelVersion);
        jdbcTemplate.update("""
                INSERT INTO relationship_evidence
                    (relationship_id, document_id, evidence_id, contribution_score,
                     model_version, formula_version)
                VALUES (?, ?, ?, CAST(? AS NUMERIC), 'model-v1', 'formula-v1')
                """, RELATIONSHIP_AB, documentId, evidenceId, contribution);
    }
}
