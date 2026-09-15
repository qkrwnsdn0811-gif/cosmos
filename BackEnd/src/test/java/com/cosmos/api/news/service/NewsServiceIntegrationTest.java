package com.cosmos.api.news.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.cosmos.api.company.error.CompanyErrorCode;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.news.dto.NewsDetailResponse;
import com.cosmos.api.news.dto.NewsSummaryResponse;
import com.cosmos.api.news.error.NewsErrorCode;
import com.cosmos.api.news.repository.NewsSearchCondition;
import java.time.LocalDate;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

@SpringBootTest
@Transactional
class NewsServiceIntegrationTest {

    private static final UUID COMPANY_A = UUID.fromString("70000000-0000-0000-0000-000000000001");
    private static final UUID COMPANY_B = UUID.fromString("70000000-0000-0000-0000-000000000002");
    private static final UUID MISSING_COMPANY = UUID.fromString("70000000-0000-0000-0000-000000000003");
    private static final UUID INDUSTRY_A = UUID.fromString("70000000-0000-0000-0000-000000000010");
    private static final UUID INDUSTRY_B = UUID.fromString("70000000-0000-0000-0000-000000000011");
    private static final UUID NEWS_NEW = UUID.fromString("70000000-0000-0000-0000-000000000020");
    private static final UUID NEWS_OLD = UUID.fromString("70000000-0000-0000-0000-000000000021");
    private static final UUID NEWS_HIDDEN = UUID.fromString("70000000-0000-0000-0000-000000000022");
    private static final UUID SOURCE_ID = UUID.fromString("70000000-0000-0000-0000-000000000030");
    private static final long USER_ID = 700001L;

    @Autowired
    private NewsService newsService;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @BeforeEach
    void setUp() {
        jdbcTemplate.update("INSERT INTO industry (industry_id, name) VALUES (?, '반도체')", INDUSTRY_A);
        jdbcTemplate.update("INSERT INTO industry (industry_id, name) VALUES (?, '배터리')", INDUSTRY_B);
        insertCompany(COMPANY_A, "기업A", "A700", "KOSPI");
        insertCompany(COMPANY_B, "기업B", "B700", "NASDAQ");
        jdbcTemplate.update("INSERT INTO company_industry VALUES (?, ?, TRUE)", COMPANY_A, INDUSTRY_A);
        jdbcTemplate.update("INSERT INTO company_industry VALUES (?, ?, TRUE)", COMPANY_B, INDUSTRY_B);
        jdbcTemplate.update("""
                INSERT INTO data_source (source_id, name, source_type)
                VALUES (?, '뉴스 서비스 테스트', 'NEWS')
                """, SOURCE_ID);

        insertNews(NEWS_NEW, "반도체 공급망 협력 확대", "2026-09-07T02:00:00Z", "홍길동", 'a');
        insertNews(NEWS_OLD, "과거 중립 뉴스", "2026-09-05T02:00:00Z", null, 'b');
        insertNews(NEWS_HIDDEN, "숨김 뉴스", "2026-09-08T02:00:00Z", null, 'c');
        insertCompanyDocument(NEWS_NEW, COMPANY_A, "POSITIVE", "0.94", "0.81", true);
        insertCompanyDocument(NEWS_NEW, COMPANY_B, "NEGATIVE", "0.80", "-0.42", true);
        insertCompanyDocument(NEWS_OLD, COMPANY_A, "NEUTRAL", "0.75", "0.02", true);
        insertCompanyDocument(NEWS_HIDDEN, COMPANY_A, "POSITIVE", "0.99", "0.90", false);
        jdbcTemplate.update("""
                INSERT INTO document_evidence
                    (evidence_id, document_id, company_id, sentence_text, sentence_order, confidence)
                VALUES (?, ?, ?, '양사는 공급 협력을 확대한다.', 0, 0.91)
                """, UUID.fromString("70000000-0000-0000-0000-000000000040"), NEWS_NEW, COMPANY_A);
        jdbcTemplate.update("""
                INSERT INTO users (user_id, email, password, nickname)
                VALUES (?, 'news-test@example.com', 'encoded-password', '뉴스테스터')
                """, USER_ID);
        jdbcTemplate.update("INSERT INTO user_scrap (user_id, document_id) VALUES (?, ?)", USER_ID, NEWS_NEW);
    }

    @Test
    void returnsOnlyVisibleNewsNewestFirstForAnonymousUser() {
        CursorPageResponse<NewsSummaryResponse> result = newsService.findNews(condition(null, 20), null);

        assertThat(result.items()).extracting(NewsSummaryResponse::newsId)
                .containsExactly(NEWS_NEW, NEWS_OLD);
        assertThat(result.items().getFirst().sentiment()).isEqualTo("POSITIVE");
        assertThat(result.items().getFirst().relatedCompanies()).hasSize(2);
        assertThat(result.items()).allMatch(item -> !item.scrapped());
    }

    @Test
    void returnsScrappedForAuthenticatedUser() {
        CursorPageResponse<NewsSummaryResponse> result = newsService.findNews(condition(null, 20), USER_ID);

        assertThat(result.items().getFirst().newsId()).isEqualTo(NEWS_NEW);
        assertThat(result.items().getFirst().scrapped()).isTrue();
        assertThat(result.items().get(1).scrapped()).isFalse();
    }

    @Test
    void appliesKeywordCompanyIndustrySentimentAndDateFilters() {
        NewsSearchCondition condition = new NewsSearchCondition(
                "공급망", COMPANY_B, INDUSTRY_B, "negative",
                LocalDate.parse("2026-09-07"), LocalDate.parse("2026-09-07"), null, 20);

        CursorPageResponse<NewsSummaryResponse> result = newsService.findNews(condition, null);

        assertThat(result.items()).singleElement().satisfies(item -> {
            assertThat(item.newsId()).isEqualTo(NEWS_NEW);
            assertThat(item.sentiment()).isEqualTo("NEGATIVE");
        });
    }

    @Test
    void paginatesWithPublishedAtAndNewsIdCursor() {
        CursorPageResponse<NewsSummaryResponse> first = newsService.findNews(condition(null, 1), null);
        assertThat(first.items()).extracting(NewsSummaryResponse::newsId).containsExactly(NEWS_NEW);
        assertThat(first.hasNext()).isTrue();
        assertThat(first.nextCursor()).isNotBlank();

        NewsSearchCondition secondCondition = new NewsSearchCondition(
                null, null, null, null, null, null, first.nextCursor(), 1);
        CursorPageResponse<NewsSummaryResponse> second = newsService.findNews(secondCondition, null);
        assertThat(second.items()).extracting(NewsSummaryResponse::newsId).containsExactly(NEWS_OLD);
        assertThat(second.hasNext()).isFalse();
    }

    @Test
    void returnsNewsDetailWithoutRawBody() {
        NewsDetailResponse result = newsService.findNewsDetail(NEWS_NEW, USER_ID);

        assertThat(result.newsId()).isEqualTo(NEWS_NEW);
        assertThat(result.author()).isEqualTo("홍길동");
        assertThat(result.relatedCompanies()).extracting(company -> company.companyId())
                .containsExactly(COMPANY_A, COMPANY_B);
        assertThat(result.relatedCompanies().getFirst().relevanceScore()).isEqualByComparingTo("0.940000");
        assertThat(result.evidence()).singleElement()
                .satisfies(evidence -> {
                    assertThat(evidence.sentence()).isEqualTo("양사는 공급 협력을 확대한다.");
                    assertThat(evidence.confidence()).isEqualByComparingTo("0.910000");
                });
        assertThat(result.scrapped()).isTrue();
    }

    @Test
    void rejectsInvalidFiltersAndMissingResources() {
        assertError(
                () -> newsService.findNews(new NewsSearchCondition(
                        null, null, null, "UNKNOWN", null, null, null, 20), null),
                CommonErrorCode.VALIDATION_FAILED);
        assertError(
                () -> newsService.findNews(new NewsSearchCondition(
                        null, null, null, null,
                        LocalDate.parse("2026-09-08"), LocalDate.parse("2026-09-07"), null, 20), null),
                CommonErrorCode.VALIDATION_FAILED);
        assertError(
                () -> newsService.findNews(new NewsSearchCondition(
                        null, MISSING_COMPANY, null, null, null, null, null, 20), null),
                CompanyErrorCode.COMPANY_NOT_FOUND);
        assertError(
                () -> newsService.findNews(new NewsSearchCondition(
                        null, null, null, null, null, null, "invalid", 20), null),
                CommonErrorCode.INVALID_CURSOR);
        assertError(() -> newsService.findNewsDetail(NEWS_HIDDEN, null), NewsErrorCode.NEWS_NOT_FOUND);
        assertError(
                () -> newsService.findNewsDetail(UUID.fromString("70000000-0000-0000-0000-000000000099"), null),
                NewsErrorCode.NEWS_NOT_FOUND);
    }

    private NewsSearchCondition condition(String cursor, int size) {
        return new NewsSearchCondition(null, null, null, null, null, null, cursor, size);
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

    private void insertNews(
            UUID newsId,
            String title,
            String publishedAt,
            String author,
            char hashCharacter
    ) {
        jdbcTemplate.update("""
                INSERT INTO source_document
                    (document_id, source_id, document_type, title, summary, original_url,
                     published_at, status)
                VALUES (?, ?, 'NEWS', ?, '기사 요약', ?, CAST(? AS TIMESTAMPTZ), 'ANALYZED')
                """, newsId, SOURCE_ID, title, "https://example.com/" + newsId, publishedAt);
        jdbcTemplate.update("""
                INSERT INTO news_article
                    (document_id, publisher, canonical_url, canonical_url_hash, author)
                VALUES (?, '예시경제', ?, ?, ?)
                """, newsId, "https://example.com/" + newsId,
                String.valueOf(hashCharacter).repeat(64), author);
    }

    private void insertCompanyDocument(
            UUID newsId,
            UUID companyId,
            String sentiment,
            String relevance,
            String impact,
            boolean visible
    ) {
        jdbcTemplate.update("""
                INSERT INTO company_document
                    (document_id, company_id, relevance_score, sentiment, impact_score,
                     confidence, model_version, is_service_visible)
                VALUES (?, ?, CAST(? AS NUMERIC), ?, CAST(? AS NUMERIC), 0.9, 'test-v1', ?)
                """, newsId, companyId, relevance, sentiment, impact, visible);
    }
}
