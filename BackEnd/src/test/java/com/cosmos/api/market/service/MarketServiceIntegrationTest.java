package com.cosmos.api.market.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.within;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.market.dto.IndexPointResponse;
import com.cosmos.api.market.dto.MarketIndexListResponse;
import com.cosmos.api.market.dto.MarketIndexResponse;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

/** 지수 테이블은 테스트 트랜잭션 안에서 비우고 70거래일을 넣는다(끝나면 되돌린다). */
@SpringBootTest
@Transactional
class MarketServiceIntegrationTest {

    @Autowired
    private MarketService marketService;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    private final List<LocalDate> days = new ArrayList<>();

    @BeforeEach
    void setUp() {
        jdbcTemplate.update("DELETE FROM market_index_price");
        LocalDate day = LocalDate.of(2025, 1, 2);
        while (days.size() < 70) {
            if (day.getDayOfWeek() != DayOfWeek.SATURDAY && day.getDayOfWeek() != DayOfWeek.SUNDAY) {
                days.add(day);
            }
            day = day.plusDays(1);
        }
        for (int i = 0; i < days.size(); i++) {
            insertIndex("KOSPI", days.get(i), 1000 + i);
            insertIndex("KOSDAQ", days.get(i), 500 + i);
        }
    }

    @Test
    void indices_3m_has_61_points() {
        MarketIndexListResponse result = marketService.findIndices("3M");

        assertThat(result.asOf()).isEqualTo(days.get(69));
        assertThat(result.items()).extracting(MarketIndexResponse::indexCode).containsExactly("KOSPI", "KOSDAQ");
        for (MarketIndexResponse index : result.items()) {
            assertThat(index.points()).hasSize(61);
            assertThat(index.points().get(0).date()).isEqualTo(days.get(9));
            assertThat(index.points().get(60).date()).isEqualTo(days.get(69));
        }
        assertThat(result.items().get(0).points()).extracting(IndexPointResponse::close)
                .first().satisfies(close -> assertThat(close.intValue()).isEqualTo(1009));
    }

    @Test
    void indices_changes() {
        MarketIndexResponse kospi = marketService.findIndices("1M").items().get(0);

        assertThat(kospi.close().intValue()).isEqualTo(1069);
        assertThat(kospi.change1d()).isCloseTo(1069.0 / 1068.0 - 1, within(1e-9));
        assertThat(kospi.change1m()).isCloseTo(1069.0 / 1049.0 - 1, within(1e-9));
        assertThat(kospi.points()).hasSize(21);
    }

    @Test
    void indices_bad_period_400() {
        assertThatThrownBy(() -> marketService.findIndices("2W")).isInstanceOf(BusinessException.class);
    }

    private void insertIndex(String code, LocalDate date, int close) {
        jdbcTemplate.update("INSERT INTO market_index_price (index_code, trading_date, close_price) VALUES (?, ?, ?)",
                code, date, close);
    }
}
