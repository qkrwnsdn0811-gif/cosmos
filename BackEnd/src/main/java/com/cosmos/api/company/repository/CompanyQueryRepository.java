package com.cosmos.api.company.repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
public class CompanyQueryRepository {

    private static final String COMPANY_SEARCH_SQL = """
            WITH candidates AS (
                SELECT
                    c.company_id,
                    c.name,
                    c.name_en,
                    c.stock_code,
                    c.market,
                    CASE
                        WHEN CAST(:keyword AS TEXT) IS NULL THEN 0
                        WHEN lower(c.stock_code) = :keyword THEN 0
                        WHEN lower(c.name) = :keyword
                            OR lower(c.name_en) = :keyword
                            OR EXISTS (
                                SELECT 1 FROM company_alias exact_alias
                                WHERE exact_alias.company_id = c.company_id
                                  AND lower(exact_alias.alias_name) = :keyword
                            ) THEN 1
                        WHEN lower(c.stock_code) LIKE :prefix_keyword ESCAPE '\\'
                            OR lower(c.name) LIKE :prefix_keyword ESCAPE '\\'
                            OR lower(c.name_en) LIKE :prefix_keyword ESCAPE '\\'
                            OR EXISTS (
                                SELECT 1 FROM company_alias prefix_alias
                                WHERE prefix_alias.company_id = c.company_id
                                  AND lower(prefix_alias.alias_name) LIKE :prefix_keyword ESCAPE '\\'
                            ) THEN 2
                        ELSE 3
                    END AS search_rank
                FROM company c
                WHERE c.status = 'ACTIVE'
                  AND (CAST(:market AS TEXT) IS NULL OR upper(c.market) = :market)
                  AND (CAST(:industry_id AS UUID) IS NULL OR EXISTS (
                      SELECT 1 FROM company_industry filter_ci
                      WHERE filter_ci.company_id = c.company_id
                        AND filter_ci.industry_id = :industry_id
                  ))
                  AND (CAST(:keyword AS TEXT) IS NULL
                      OR lower(c.stock_code) LIKE :contains_keyword ESCAPE '\\'
                      OR lower(c.name) LIKE :contains_keyword ESCAPE '\\'
                      OR lower(c.name_en) LIKE :contains_keyword ESCAPE '\\'
                      OR EXISTS (
                          SELECT 1 FROM company_alias search_alias
                          WHERE search_alias.company_id = c.company_id
                            AND lower(search_alias.alias_name) LIKE :contains_keyword ESCAPE '\\'
                      ))
            )
            SELECT
                c.company_id,
                c.name,
                c.name_en,
                c.stock_code,
                c.market,
                i.industry_id AS primary_industry_id,
                i.name AS primary_industry_name,
                c.search_rank
            FROM candidates c
            JOIN company_industry ci
              ON ci.company_id = c.company_id
             AND ci.is_primary = TRUE
            JOIN industry i ON i.industry_id = ci.industry_id
            WHERE (CAST(:cursor_rank AS INTEGER) IS NULL
                OR c.search_rank > :cursor_rank
                OR (c.search_rank = :cursor_rank AND c.name > :cursor_name)
                OR (c.search_rank = :cursor_rank AND c.name = :cursor_name AND c.company_id > :cursor_id))
            ORDER BY c.search_rank ASC, c.name ASC, c.company_id ASC
            LIMIT :limit
            """;

    private static final String INDUSTRY_LIST_SQL = """
            SELECT
                i.industry_id,
                i.parent_industry_id,
                i.name,
                i.description,
                COUNT(DISTINCT c.company_id) AS company_count
            FROM industry i
            LEFT JOIN company_industry ci ON ci.industry_id = i.industry_id
            LEFT JOIN company c
              ON c.company_id = ci.company_id
             AND c.status = 'ACTIVE'
            GROUP BY i.industry_id, i.parent_industry_id, i.name, i.description
            ORDER BY i.name ASC, i.industry_id ASC
            """;

    private final NamedParameterJdbcTemplate jdbcTemplate;

    public CompanyQueryRepository(NamedParameterJdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public List<CompanySearchRow> search(
            CompanySearchCondition condition,
            Integer cursorRank,
            String cursorName,
            java.util.UUID cursorId
    ) {
        Map<String, Object> parameters = new HashMap<>();
        parameters.put("keyword", condition.keyword());
        parameters.put("market", condition.market());
        parameters.put("industry_id", condition.industryId());
        parameters.put("prefix_keyword", likePattern(condition.keyword(), false));
        parameters.put("contains_keyword", likePattern(condition.keyword(), true));
        parameters.put("cursor_rank", cursorRank);
        parameters.put("cursor_name", cursorName);
        parameters.put("cursor_id", cursorId);
        parameters.put("limit", condition.size() + 1);

        return jdbcTemplate.query(COMPANY_SEARCH_SQL, parameters, this::mapCompanyRow);
    }

    public List<IndustryListRow> findIndustries() {
        return jdbcTemplate.query(INDUSTRY_LIST_SQL, Map.of(), this::mapIndustryRow);
    }

    private CompanySearchRow mapCompanyRow(ResultSet resultSet, int rowNumber) throws SQLException {
        return new CompanySearchRow(
                resultSet.getObject("company_id", java.util.UUID.class),
                resultSet.getString("name"),
                resultSet.getString("name_en"),
                resultSet.getString("stock_code"),
                resultSet.getString("market"),
                resultSet.getObject("primary_industry_id", java.util.UUID.class),
                resultSet.getString("primary_industry_name"),
                resultSet.getInt("search_rank"));
    }

    private IndustryListRow mapIndustryRow(ResultSet resultSet, int rowNumber) throws SQLException {
        return new IndustryListRow(
                resultSet.getObject("industry_id", java.util.UUID.class),
                resultSet.getObject("parent_industry_id", java.util.UUID.class),
                resultSet.getString("name"),
                resultSet.getString("description"),
                resultSet.getLong("company_count"));
    }

    private String likePattern(String keyword, boolean contains) {
        if (keyword == null) {
            return null;
        }
        String escaped = keyword
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_");
        return contains ? "%" + escaped + "%" : escaped + "%";
    }
}
