package com.cosmos.api.company.repository;

import com.cosmos.api.company.dto.CompanyFundamentalResponse;
import java.sql.ResultSet;
import java.time.LocalDate;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

/** 기업 지표(V12 company_fundamental) 조회. 쓰기는 파이프라인(nexus fundamentals)이 한다. 이벤트 지도도 이걸 쓴다. */
@Repository
public class CompanyFundamentalQueryRepository {

    private final NamedParameterJdbcTemplate jdbcTemplate;

    public CompanyFundamentalQueryRepository(NamedParameterJdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /** 기업 → 지표. 지표 행이 없는 기업은 결과에 없다. */
    public Map<UUID, CompanyFundamentalResponse> findByCompanyIds(Collection<UUID> companyIds) {
        Map<UUID, CompanyFundamentalResponse> result = new LinkedHashMap<>();
        if (companyIds.isEmpty()) {
            return result;
        }
        jdbcTemplate.query("""
                SELECT company_id, market_cap, market_cap_rank, size_class, per, pbr, dividend_yield, net_income_ttm,
                       equity, dividend_per_share, close_price, price_date, fiscal_basis
                FROM company_fundamental
                WHERE company_id IN (:companyIds)
                """, new MapSqlParameterSource("companyIds", companyIds), (ResultSet rs) -> {
            result.put(rs.getObject("company_id", UUID.class), new CompanyFundamentalResponse(
                    rs.getBigDecimal("market_cap"),
                    rs.getObject("market_cap_rank", Integer.class),
                    rs.getString("size_class"),
                    rs.getBigDecimal("per"),
                    rs.getBigDecimal("pbr"),
                    rs.getBigDecimal("dividend_yield"),
                    rs.getBigDecimal("net_income_ttm"),
                    rs.getBigDecimal("equity"),
                    rs.getBigDecimal("dividend_per_share"),
                    rs.getBigDecimal("close_price"),
                    rs.getObject("price_date", LocalDate.class),
                    rs.getString("fiscal_basis")));
        });
        return result;
    }
}
