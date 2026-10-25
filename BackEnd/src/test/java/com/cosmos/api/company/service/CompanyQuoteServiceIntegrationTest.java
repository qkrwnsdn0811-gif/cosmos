package com.cosmos.api.company.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.within;

import com.cosmos.api.company.dto.CompanyQuoteListResponse;
import com.cosmos.api.company.dto.CompanyQuoteResponse;
import com.cosmos.api.company.dto.RelatedCompanyListResponse;
import com.cosmos.api.company.dto.RelatedCompanyResponse;
import com.cosmos.api.company.error.CompanyErrorCode;
import com.cosmos.api.global.error.BusinessException;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

/**
 * Q1: 일봉 61개(종가 100…160), 재무 있음 · Q2: 일봉 15개, 재무 없음 · Q3: 일봉 없음, 재무만 · Q4: 일봉 61개, 적자.
 * 공개 스냅샷 1Y 관계: Q1→Q2 공급 70, Q1–Q4 함께 언급 40, Q4→Q1 공급 90. 30D 관계: Q1→Q3 공급.
 */
@SpringBootTest
@Transactional
class CompanyQuoteServiceIntegrationTest {

    private static final UUID Q1 = id(1);
    private static final UUID Q2 = id(2);
    private static final UUID Q3 = id(3);
    private static final UUID Q4 = id(4);
    private static final UUID INDUSTRY = id(10);
    private static final UUID SNAPSHOT = id(20);
    private static final UUID SOURCE = id(21);

    @Autowired
    private CompanyQuoteService quoteService;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    private final List<LocalDate> days = new ArrayList<>();

    private static UUID id(int n) {
        return UUID.fromString(String.format("7f000000-0000-0000-0000-%012d", n));
    }

    @BeforeEach
    void setUp() {
        LocalDate day = LocalDate.of(2026, 7, 1);
        while (days.size() < 61) {
            if (day.getDayOfWeek() != DayOfWeek.SATURDAY && day.getDayOfWeek() != DayOfWeek.SUNDAY) {
                days.add(day);
            }
            day = day.plusDays(1);
        }
        insertCompany(Q1, "시세테스트1", "980001", "KOSPI");
        insertCompany(Q2, "시세테스트2", "980002", "KOSDAQ");
        insertCompany(Q3, "시세테스트3", "980003", "KOSDAQ");
        insertCompany(Q4, "시세테스트4", "980004", "KOSDAQ");
        jdbcTemplate.update("INSERT INTO industry (industry_id, name) VALUES (?, '시세테스트 업종')", INDUSTRY);
        jdbcTemplate.update("INSERT INTO company_industry (company_id, industry_id, is_primary) VALUES (?, ?, TRUE)", Q1, INDUSTRY);
        for (int i = 0; i < 61; i++) {
            insertClose(Q1, days.get(i), 100 + i);
            insertClose(Q4, days.get(i), 50 + i);
        }
        for (int i = 0; i < 15; i++) {
            insertClose(Q2, days.get(46 + i), 200 + i);
        }
        insertFundamental(Q1, "5000000000000", 1, "LARGE", "10", "500000000000");
        insertFundamental(Q3, "100000000000", 3, "SMALL", "20", "5000000000");
        insertFundamental(Q4, "200000000000", 2, "SMALL", null, "-1000000000");

        for (String[] t : new String[][] {{"SUPPLY", "공급", "DIRECTED"}, {"CO_MENTIONED", "함께 언급", "UNDIRECTED"}}) {
            jdbcTemplate.update("INSERT INTO relationship_type (code, name, directionality) VALUES (?, ?, ?) "
                    + "ON CONFLICT (code) DO NOTHING", t[0], t[1], t[2]);
        }
        jdbcTemplate.update("""
                INSERT INTO graph_snapshot (snapshot_id, as_of_at, formula_version, model_version, status, hdfs_uri, published_at)
                VALUES (?, '2099-01-01T00:00:00Z', 'quote-test', 'quote-test', 'PUBLISHED', 'hdfs://quote/test',
                        '2099-01-01T00:00:00Z')
                """, SNAPSHOT);
        insertRelation(id(31), Q1, Q2, "SUPPLY", "1Y", "70");
        insertRelation(id(32), Q1, Q4, "CO_MENTIONED", "1Y", "40");
        insertRelation(id(33), Q4, Q1, "SUPPLY", "1Y", "90");
        insertRelation(id(34), Q1, Q3, "SUPPLY", "30D", "95");
    }

    @Test
    void quotes_changes_and_spark() {
        CompanyQuoteResponse q1 = byId().get(Q1);

        assertThat(q1.close()).isEqualByComparingTo("160");
        assertThat(q1.prevClose()).isEqualByComparingTo("159");
        assertThat(q1.tradingDate()).isEqualTo(days.get(60));
        assertThat(q1.change1d()).isCloseTo(160.0 / 159.0 - 1, within(1e-9));
        assertThat(q1.change1m()).isCloseTo(160.0 / 140.0 - 1, within(1e-9));
        assertThat(q1.change3m()).isCloseTo(160.0 / 100.0 - 1, within(1e-9));
        assertThat(q1.spark()).hasSize(21);
        assertThat(q1.spark().get(0)).isEqualByComparingTo("140");
        assertThat(q1.spark().get(20)).isEqualByComparingTo("160");
        assertThat(q1.industry()).isEqualTo("시세테스트 업종");
        assertThat(q1.per()).isEqualByComparingTo("10");
        assertThat(q1.marketCapRank()).isEqualTo(1);
    }

    @Test
    void quotes_with_short_history() {
        CompanyQuoteResponse q2 = byId().get(Q2);

        assertThat(q2.change1d()).isCloseTo(214.0 / 213.0 - 1, within(1e-9));
        assertThat(q2.change1m()).isNull();
        assertThat(q2.change3m()).isNull();
        assertThat(q2.spark()).hasSize(15);
        assertThat(q2.marketCap()).isNull();
    }

    @Test
    void quotes_without_prices_or_fundamentals() {
        CompanyQuoteResponse q3 = byId().get(Q3);

        assertThat(q3.close()).isNull();
        assertThat(q3.prevClose()).isNull();
        assertThat(q3.tradingDate()).isNull();
        assertThat(q3.change1d()).isNull();
        assertThat(q3.spark()).isEmpty();
        assertThat(q3.per()).isEqualByComparingTo("20");
        assertThat(byId().get(Q4).per()).isNull();
        assertThat(byId().get(Q4).netIncomeTtm()).isEqualByComparingTo("-1000000000");
    }

    @Test
    void quotes_relation_count_uses_1y_only() {
        Map<UUID, CompanyQuoteResponse> quotes = byId();

        assertThat(quotes.get(Q1).relationCount()).isEqualTo(2);
        assertThat(quotes.get(Q2).relationCount()).isEqualTo(1);
        assertThat(quotes.get(Q3).relationCount()).isZero();
        assertThat(quotes.get(Q4).relationCount()).isEqualTo(1);
    }

    @Test
    void relation_count_skips_inactive_counterparts() {
        // 상세의 관계 기업 목록(활성 기업만)과 같은 기준이어야 한다
        UUID inactive = id(5);
        jdbcTemplate.update("INSERT INTO company (company_id, name, stock_code, market, status) VALUES (?, '시세테스트5', '980005', 'KOSDAQ', 'INACTIVE')",
                inactive);
        insertRelation(id(36), Q1, inactive, "SUPPLY", "1Y", "50");

        assertThat(byId().get(Q1).relationCount()).isEqualTo(2);
        assertThat(quoteService.findRelated(Q1).items()).extracting(r -> r.quote().companyId()).doesNotContain(inactive);
    }

    @Test
    void quotes_sorted_by_market_cap() {
        CompanyQuoteListResponse result = quoteService.findQuotes();

        List<UUID> mine = result.items().stream().map(CompanyQuoteResponse::companyId)
                .filter(List.of(Q1, Q2, Q3, Q4)::contains).toList();
        assertThat(mine).containsExactly(Q1, Q4, Q3, Q2);
        assertThat(result.asOf()).isAfterOrEqualTo(days.get(60));
    }

    @Test
    void quotes_news_counts_for_watch() {
        // 마지막 뉴스 2099-03-15 기준: Q1 은 최근 7일 6건, 그 전 60일 3건 → 평소 7일 3/60×7. Q2 는 최근 1건
        for (int i = 0; i < 6; i++) {
            insertReported(id(100 + i), Q1, "2099-03-%02dT03:00:00Z".formatted(10 + i));
        }
        for (int i = 0; i < 3; i++) {
            insertReported(id(110 + i), Q1, "2099-02-%02dT03:00:00Z".formatted(1 + i));
        }
        insertReported(id(120), Q2, "2099-03-15T03:00:00Z");

        Map<UUID, CompanyQuoteResponse> quotes = byId();
        assertThat(quotes.get(Q1).news7d()).isEqualTo(6);
        assertThat(quotes.get(Q1).newsUsual7d()).isCloseTo(3.0 / 60 * 7, within(1e-9));
        assertThat(quotes.get(Q2).news7d()).isEqualTo(1);
        assertThat(quotes.get(Q2).newsUsual7d()).isZero();
        assertThat(quotes.get(Q3).news7d()).isZero();
    }

    @Test
    void quotes_52_week_range() {
        CompanyQuoteResponse q1 = byId().get(Q1);

        assertThat(q1.high52w()).isEqualByComparingTo("160");
        assertThat(q1.low52w()).isEqualByComparingTo("100");
        assertThat(byId().get(Q3).high52w()).isNull();
    }

    @Test
    void quotes_expected_moves_semiconductor_only() {
        insertMove("SEMICONDUCTOR", Q1, "SHORT", "0.038", "SMALL");
        insertMove("SEMICONDUCTOR", Q1, "LONG", "0.133", "LARGE");
        insertMove("BATTERY", Q2, "SHORT", "0.09", "LARGE");

        Map<UUID, CompanyQuoteResponse> quotes = byId();
        CompanyQuoteResponse q1 = quotes.get(Q1);
        assertThat(q1.expectedMoveShort()).isCloseTo(0.038, within(1e-9));
        assertThat(q1.moveGradeShort()).isEqualTo("SMALL");
        assertThat(q1.expectedMoveLong()).isCloseTo(0.133, within(1e-9));
        assertThat(q1.moveGradeLong()).isEqualTo("LARGE");
        assertThat(quotes.get(Q2).expectedMoveShort()).isNull();
        assertThat(quotes.get(Q2).moveGradeShort()).isNull();
        assertThat(quotes.get(Q3).moveGradeLong()).isNull();
    }

    @Test
    void related_directions() {
        Map<UUID, RelatedCompanyResponse> related = relatedOf(Q1);

        assertThat(related.get(Q4).relationType()).isEqualTo("SUPPLY");
        assertThat(related.get(Q4).direction()).isEqualTo("IN");
        assertThat(related.get(Q2).relationType()).isEqualTo("SUPPLY");
        assertThat(related.get(Q2).direction()).isEqualTo("OUT");
        assertThat(related.get(Q2).quote().close()).isEqualByComparingTo("214");
    }

    @Test
    void related_merges_same_counterpart() {
        RelatedCompanyListResponse result = quoteService.findRelated(Q1);

        assertThat(result.items()).filteredOn(r -> r.quote().companyId().equals(Q4)).hasSize(1);
        RelatedCompanyResponse q4 = relatedOf(Q1).get(Q4);
        assertThat(q4.score()).isCloseTo(90.0, within(1e-9));
        assertThat(q4.otherTypes()).containsExactly("CO_MENTIONED");
    }

    @Test
    void related_sorted_and_1y_only() {
        RelatedCompanyListResponse result = quoteService.findRelated(Q1);

        assertThat(result.companyId()).isEqualTo(Q1);
        assertThat(result.window()).isEqualTo("1Y");
        assertThat(result.items()).extracting(r -> r.quote().companyId()).containsExactly(Q4, Q2);
    }

    @Test
    void related_undirected_is_none() {
        insertRelation(id(35), Q2, Q3, "CO_MENTIONED", "1Y", "10");

        RelatedCompanyResponse q3 = relatedOf(Q2).get(Q3);
        assertThat(q3.relationType()).isEqualTo("CO_MENTIONED");
        assertThat(q3.direction()).isEqualTo("NONE");
        assertThat(q3.otherTypes()).isEmpty();
    }

    @Test
    void related_unknown_company_404() {
        assertThatThrownBy(() -> quoteService.findRelated(id(99)))
                .isInstanceOf(BusinessException.class)
                .satisfies(e -> assertThat(((BusinessException) e).getErrorCode()).isEqualTo(CompanyErrorCode.COMPANY_NOT_FOUND));
    }

    private Map<UUID, RelatedCompanyResponse> relatedOf(UUID companyId) {
        return quoteService.findRelated(companyId).items().stream()
                .collect(Collectors.toMap(r -> r.quote().companyId(), Function.identity()));
    }

    private void insertReported(UUID eventId, UUID companyId, String dateline) {
        UUID documentId = UUID.fromString(eventId.toString().replace("7f000000", "7f0000d0"));
        jdbcTemplate.update("INSERT INTO data_source (source_id, name, source_type) VALUES (?, 'quote-test-source', 'NEWS_API') "
                + "ON CONFLICT DO NOTHING", SOURCE);
        jdbcTemplate.update("""
                INSERT INTO source_document (document_id, source_id, document_type, title, original_url, published_at, status)
                VALUES (?, ?, 'NEWS', '시세테스트 뉴스', 'https://example.com/' || ?, CAST(? AS TIMESTAMPTZ), 'ANALYZED')
                """, documentId, SOURCE, eventId.toString(), dateline);
        jdbcTemplate.update("""
                INSERT INTO news_event (event_id, document_id, bigkinds_news_id, value_chain, categories, dateline, pipeline_version)
                VALUES (?, ?, ?, 'AUTO', ARRAY['경제'], CAST(? AS TIMESTAMPTZ), 'test')
                """, eventId, documentId, "quote-" + eventId, dateline);
        jdbcTemplate.update("""
                INSERT INTO event_company_impact (event_id, company_id, basis, hop, path, propagation_score, status, model_version)
                VALUES (?, ?, 'REPORTED', 0, CAST('[]' AS JSONB), 1, 'OK', 'test')
                """, eventId, companyId);
    }

    private Map<UUID, CompanyQuoteResponse> byId() {
        return quoteService.findQuotes().items().stream()
                .collect(Collectors.toMap(CompanyQuoteResponse::companyId, Function.identity()));
    }

    private void insertCompany(UUID companyId, String name, String stockCode, String market) {
        jdbcTemplate.update("INSERT INTO company (company_id, name, stock_code, market, status) VALUES (?, ?, ?, ?, 'ACTIVE')",
                companyId, name, stockCode, market);
    }

    private void insertClose(UUID companyId, LocalDate date, int close) {
        jdbcTemplate.update("""
                INSERT INTO stock_price_history (company_id, trading_at, interval_type, open_price, high_price, low_price,
                                                 close_price, trading_volume)
                VALUES (?, CAST(? AS DATE)::timestamp AT TIME ZONE 'UTC', '1D', ?, ?, ?, ?, 1000)
                """, companyId, date.toString(), close, close, close, close);
    }

    private void insertFundamental(UUID companyId, String marketCap, int rank, String size, String per, String netIncome) {
        jdbcTemplate.update("""
                INSERT INTO company_fundamental (company_id, market_cap, market_cap_rank, size_class, per, pbr, dividend_yield,
                                                 net_income_ttm, equity, dividend_per_share, close_price, price_date,
                                                 fiscal_basis, source)
                VALUES (?, CAST(? AS NUMERIC), ?, ?, CAST(? AS NUMERIC), 1.5, 0.01, CAST(? AS NUMERIC), 3000000000000, 1000,
                        160, '2026-09-22', '테스트 기준', 'test')
                """, companyId, marketCap, rank, size, per, netIncome);
    }

    private void insertMove(String valueChain, UUID companyId, String horizon, String move, String grade) {
        jdbcTemplate.update("""
                INSERT INTO company_expected_move (value_chain, company_id, horizon, expected_move, case_count, grade, period_end)
                VALUES (?, ?, ?, CAST(? AS NUMERIC), 30, ?, '2026-06-30')
                """, valueChain, companyId, horizon, move, grade);
    }

    private void insertRelation(UUID relationshipId, UUID source, UUID target, String type, String window, String score) {
        jdbcTemplate.update("""
                INSERT INTO company_relationship (relationship_id, source_company_id, target_company_id, relationship_type_id)
                VALUES (?, ?, ?, (SELECT relationship_type_id FROM relationship_type WHERE code = ?))
                """, relationshipId, source, target, type);
        jdbcTemplate.update("""
                INSERT INTO relationship_score_current (relationship_id, window_type, snapshot_id, score, news_score,
                                                        confidence, evidence_count, formula_version, as_of_at)
                VALUES (?, ?, ?, CAST(? AS NUMERIC), CAST(? AS NUMERIC), 0.8, 1, 'quote-test', '2099-01-01T00:00:00Z')
                """, relationshipId, window, SNAPSHOT, score, score);
    }
}
