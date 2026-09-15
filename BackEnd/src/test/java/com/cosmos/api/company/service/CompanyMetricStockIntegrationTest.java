package com.cosmos.api.company.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.cosmos.api.company.dto.CompanyMetricHistoryResponse;
import com.cosmos.api.company.dto.CompanyMetricResponse;
import com.cosmos.api.company.dto.StockPriceHistoryResponse;
import com.cosmos.api.company.error.CompanyErrorCode;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
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
class CompanyMetricStockIntegrationTest {

    private static final UUID COMPANY_ID = UUID.fromString("40000000-0000-0000-0000-000000000001");
    private static final UUID EMPTY_COMPANY_ID = UUID.fromString("40000000-0000-0000-0000-000000000002");
    private static final UUID INACTIVE_COMPANY_ID = UUID.fromString("40000000-0000-0000-0000-000000000003");

    @Autowired
    private CompanyService companyService;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @BeforeEach
    void setUp() {
        insertCompany(COMPANY_ID, "지표테스트기업", "M00001", "ACTIVE");
        insertCompany(EMPTY_COMPANY_ID, "빈데이터기업", "M00002", "ACTIVE");
        insertCompany(INACTIVE_COMPANY_ID, "비활성기업", "M00003", "INACTIVE");

        insertMetric("2026-08-31T00:00:00Z", "30D", 100, 60, 10, "0.500000", 8);
        insertMetric("2026-09-07T06:00:00Z", "30D", 152, 91, 23, "0.620000", 18);
        insertMetric("2026-09-07T06:00:00Z", "7D", 40, 25, 5, "0.700000", 6);

        insertStock("2026-07-31T00:00:00Z", "70000", "71000", "69000", "70500", 900L);
        insertStock("2026-08-10T00:00:00Z", "71000", "72000", "70000", "71500", 1000L);
        insertStock("2026-09-06T00:00:00Z", "72000", "73500", "71500", "73000", 18234567L);
    }

    @Test
    void returnsLatestMetricForRequestedWindow() {
        CompanyMetricResponse result = companyService.findLatestMetric(COMPANY_ID, "30d");

        assertThat(result.companyId()).isEqualTo(COMPANY_ID);
        assertThat(result.window()).isEqualTo("30D");
        assertThat(result.newsMentionCount()).isEqualTo(152);
        assertThat(result.sentimentScore()).isEqualByComparingTo("0.620000");
        assertThat(result.measuredAt()).hasToString("2026-09-07T06:00:00Z");
    }

    @Test
    void keepsMetricsSeparatedByWindow() {
        CompanyMetricResponse result = companyService.findLatestMetric(COMPANY_ID, "7D");

        assertThat(result.newsMentionCount()).isEqualTo(40);
        assertThat(result.relationshipCount()).isEqualTo(6);
    }

    @Test
    void returnsNullMetricFieldsWhenNoMetricExists() {
        CompanyMetricResponse result = companyService.findLatestMetric(EMPTY_COMPANY_ID, "30D");

        assertThat(result.companyId()).isEqualTo(EMPTY_COMPANY_ID);
        assertThat(result.window()).isEqualTo("30D");
        assertThat(result.newsMentionCount()).isNull();
        assertThat(result.sentimentScore()).isNull();
        assertThat(result.measuredAt()).isNull();
    }

    @Test
    void returnsMetricHistoryWithinInclusiveDateRangeInAscendingOrder() {
        CompanyMetricHistoryResponse result = companyService.findMetricHistory(
                COMPANY_ID,
                "30D",
                LocalDate.parse("2026-08-31"),
                LocalDate.parse("2026-09-07"));

        assertThat(result.items()).hasSize(2);
        assertThat(result.items()).extracting(item -> item.newsMentionCount())
                .containsExactly(100, 152);
    }

    @Test
    void returnsStockPricesForPeriodAnchoredAtLatestAvailablePrice() {
        StockPriceHistoryResponse result = companyService.findStockPrices(COMPANY_ID, "1m", "1d");

        assertThat(result.period()).isEqualTo("1M");
        assertThat(result.interval()).isEqualTo("1D");
        assertThat(result.asOfAt()).hasToString("2026-09-06T00:00:00Z");
        assertThat(result.items()).extracting(item -> item.tradingAt().toString())
                .containsExactly("2026-08-10T00:00:00Z", "2026-09-06T00:00:00Z");
    }

    @Test
    void returnsEmptyStockResponseWhenNoPriceExists() {
        StockPriceHistoryResponse result = companyService.findStockPrices(EMPTY_COMPANY_ID, "1M", "1D");

        assertThat(result.items()).isEmpty();
        assertThat(result.asOfAt()).isNull();
    }

    @Test
    void rejectsInvalidWindowPeriodIntervalAndDateRange() {
        assertValidationFailed(() -> companyService.findLatestMetric(COMPANY_ID, "14D"));
        assertValidationFailed(() -> companyService.findStockPrices(COMPANY_ID, "2M", "1D"));
        assertValidationFailed(() -> companyService.findStockPrices(COMPANY_ID, "1M", "1H"));
        assertValidationFailed(() -> companyService.findMetricHistory(
                COMPANY_ID,
                "30D",
                LocalDate.parse("2026-09-08"),
                LocalDate.parse("2026-09-07")));
    }

    @Test
    void rejectsInactiveCompany() {
        assertThatThrownBy(() -> companyService.findLatestMetric(INACTIVE_COMPANY_ID, "30D"))
                .isInstanceOf(BusinessException.class)
                .satisfies(exception -> assertThat(((BusinessException) exception).getErrorCode())
                        .isEqualTo(CompanyErrorCode.COMPANY_NOT_FOUND));
    }

    private void assertValidationFailed(Runnable action) {
        assertThatThrownBy(action::run)
                .isInstanceOf(BusinessException.class)
                .satisfies(exception -> assertThat(((BusinessException) exception).getErrorCode())
                        .isEqualTo(CommonErrorCode.VALIDATION_FAILED));
    }

    private void insertCompany(UUID companyId, String name, String stockCode, String status) {
        jdbcTemplate.update("""
                        INSERT INTO company (company_id, name, stock_code, market, status)
                        VALUES (?, ?, ?, 'TEST_METRIC', ?)
                        """,
                companyId, name, stockCode, status);
    }

    private void insertMetric(
            String measuredAt,
            String window,
            int mentionCount,
            int positiveCount,
            int negativeCount,
            String sentimentScore,
            int relationshipCount
    ) {
        jdbcTemplate.update("""
                        INSERT INTO company_metric_history
                            (company_id, measured_at, window_type, news_mention_count, positive_count,
                             negative_count, sentiment_score, relationship_count, metric_version)
                        VALUES (?, CAST(? AS TIMESTAMPTZ), ?, ?, ?, ?, CAST(? AS NUMERIC), ?, 'test-v1')
                        """,
                COMPANY_ID,
                measuredAt,
                window,
                mentionCount,
                positiveCount,
                negativeCount,
                sentimentScore,
                relationshipCount);
    }

    private void insertStock(
            String tradingAt,
            String open,
            String high,
            String low,
            String close,
            long volume
    ) {
        jdbcTemplate.update("""
                        INSERT INTO stock_price_history
                            (company_id, trading_at, interval_type, open_price, high_price,
                             low_price, close_price, trading_volume)
                        VALUES (?, CAST(? AS TIMESTAMPTZ), '1D', CAST(? AS NUMERIC), CAST(? AS NUMERIC),
                                CAST(? AS NUMERIC), CAST(? AS NUMERIC), ?)
                        """,
                COMPANY_ID, tradingAt, open, high, low, close, volume);
    }
}
