package com.cosmos.api.market.repository;

import com.cosmos.api.market.dto.IndexPointResponse;
import java.time.LocalDate;
import java.util.List;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

/** 시장 지수(V12 market_index_price) 조회. 쓰기는 파이프라인(nexus cases)이 한다. */
@Repository
public class MarketQueryRepository {

    private final NamedParameterJdbcTemplate jdbcTemplate;

    public MarketQueryRepository(NamedParameterJdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /** 지수의 최근 종가 limit 개, 최근 것부터. */
    public List<IndexPointResponse> findRecentCloses(String indexCode, int limit) {
        return jdbcTemplate.query("""
                SELECT trading_date, close_price
                FROM market_index_price
                WHERE index_code = :indexCode
                ORDER BY trading_date DESC
                LIMIT :limit
                """, new MapSqlParameterSource().addValue("indexCode", indexCode).addValue("limit", limit),
                (rs, i) -> new IndexPointResponse(rs.getObject("trading_date", LocalDate.class),
                        rs.getBigDecimal("close_price")));
    }
}
