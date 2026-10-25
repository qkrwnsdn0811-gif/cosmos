package com.cosmos.api.company.repository;

import java.math.BigDecimal;
import java.sql.ResultSet;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

/** 기업 탭 시세판 조회: 활성 기업 + 대표 업종 + 지표, 최근 일봉 종가, 공개 스냅샷 관계. */
@Repository
public class CompanyQuoteQueryRepository {

    /** 공개 스냅샷 하나(가장 최근 기준 시각). 관계 화면들과 같은 규칙이다 */
    private static final String LATEST_SNAPSHOT = """
            SELECT snapshot_id FROM graph_snapshot WHERE status = 'PUBLISHED'
            ORDER BY as_of_at DESC, snapshot_id DESC LIMIT 1""";

    private final NamedParameterJdbcTemplate jdbcTemplate;

    public CompanyQuoteQueryRepository(NamedParameterJdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public record CompanyRow(UUID companyId, String name, String stockCode, String market, String industry,
                             BigDecimal marketCap, Integer marketCapRank, String sizeClass, BigDecimal per, BigDecimal pbr,
                             BigDecimal dividendYield, BigDecimal dividendPerShare, BigDecimal netIncomeTtm,
                             BigDecimal equity, String fiscalBasis, LocalDate priceDate) {
    }

    public record DailyClose(LocalDate date, BigDecimal close) {
    }

    public List<CompanyRow> findActiveCompanies() {
        return jdbcTemplate.query("""
                SELECT c.company_id, c.name, c.stock_code, c.market, ind.name AS industry,
                       f.market_cap, f.market_cap_rank, f.size_class, f.per, f.pbr, f.dividend_yield, f.dividend_per_share,
                       f.net_income_ttm, f.equity, f.fiscal_basis, f.price_date
                FROM company c
                LEFT JOIN company_fundamental f ON f.company_id = c.company_id
                LEFT JOIN LATERAL (
                    SELECT i.name FROM company_industry ci JOIN industry i ON i.industry_id = ci.industry_id
                    WHERE ci.company_id = c.company_id AND ci.is_primary
                    ORDER BY i.name LIMIT 1
                ) ind ON TRUE
                WHERE c.status = 'ACTIVE'
                """, new MapSqlParameterSource(), (rs, i) -> new CompanyRow(
                rs.getObject("company_id", UUID.class), rs.getString("name"), rs.getString("stock_code"),
                rs.getString("market"), rs.getString("industry"), rs.getBigDecimal("market_cap"),
                rs.getObject("market_cap_rank", Integer.class), rs.getString("size_class"), rs.getBigDecimal("per"),
                rs.getBigDecimal("pbr"), rs.getBigDecimal("dividend_yield"), rs.getBigDecimal("dividend_per_share"),
                rs.getBigDecimal("net_income_ttm"), rs.getBigDecimal("equity"), rs.getString("fiscal_basis"),
                rs.getObject("price_date", LocalDate.class)));
    }

    /** 기업 → 최근 일봉 종가 limit 개, 최근 것부터. 거래일은 trading_at 의 UTC 날짜 */
    public Map<UUID, List<DailyClose>> findRecentDailyCloses(int limit) {
        Map<UUID, List<DailyClose>> result = new HashMap<>();
        jdbcTemplate.query("""
                SELECT company_id, day, close_price FROM (
                    SELECT company_id, (trading_at AT TIME ZONE 'UTC')::date AS day, close_price,
                           ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY trading_at DESC) AS rn
                    FROM stock_price_history
                    WHERE interval_type = '1D'
                ) recent
                WHERE rn <= :limit
                ORDER BY company_id, day DESC
                """, new MapSqlParameterSource("limit", limit), (ResultSet rs) -> {
            result.computeIfAbsent(rs.getObject("company_id", UUID.class), k -> new ArrayList<>())
                    .add(new DailyClose(rs.getObject("day", LocalDate.class), rs.getBigDecimal("close_price")));
        });
        return result;
    }

    /** 기업 → 공개 스냅샷의 window 관계에서 만나는 상대 기업 수 */
    public Map<UUID, Integer> findRelationCounts(String window) {
        Map<UUID, Integer> result = new HashMap<>();
        jdbcTemplate.query("""
                WITH snap AS (%s),
                edges AS (
                    SELECT cr.source_company_id, cr.target_company_id
                    FROM relationship_score_current rsc
                    JOIN snap ON snap.snapshot_id = rsc.snapshot_id
                    JOIN company_relationship cr ON cr.relationship_id = rsc.relationship_id
                    WHERE rsc.window_type = :window
                ),
                pairs AS (
                    SELECT source_company_id AS company_id, target_company_id AS other FROM edges
                    UNION ALL
                    SELECT target_company_id, source_company_id FROM edges
                )
                SELECT company_id, COUNT(DISTINCT other) AS n FROM pairs GROUP BY company_id
                """.formatted(LATEST_SNAPSHOT), new MapSqlParameterSource("window", window), (ResultSet rs) -> {
            result.put(rs.getObject("company_id", UUID.class), rs.getInt("n"));
        });
        return result;
    }
}
