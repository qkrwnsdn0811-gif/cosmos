package com.cosmos.api.news.repository;

import java.math.BigDecimal;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
public class NewsQueryRepository {

    private final NamedParameterJdbcTemplate jdbcTemplate;

    public NewsQueryRepository(NamedParameterJdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public List<RelatedCompanyRow> findRelatedCompanies(List<UUID> newsIds) {
        if (newsIds.isEmpty()) {
            return List.of();
        }
        return jdbcTemplate.query("""
                        SELECT cd.document_id AS news_id, c.company_id, c.name,
                               cd.sentiment, cd.relevance_score, cd.impact_score
                        FROM company_document cd
                        JOIN company c ON c.company_id = cd.company_id
                        WHERE cd.document_id IN (:newsIds)
                          AND cd.is_service_visible = TRUE
                          AND c.status = 'ACTIVE'
                        ORDER BY cd.document_id, cd.relevance_score DESC NULLS LAST, c.company_id
                        """,
                Map.of("newsIds", newsIds), this::mapRelatedCompany);
    }

    public Optional<NewsDetailRow> findDetail(UUID newsId, Long userId) {
        return jdbcTemplate.query("""
                        SELECT sd.document_id AS news_id, sd.title, sd.summary, na.publisher,
                               na.author, sd.original_url, sd.published_at,
                               EXISTS (
                                   SELECT 1 FROM user_scrap us
                                   WHERE us.document_id = sd.document_id
                                     AND us.user_id = CAST(:userId AS BIGINT)
                               ) AS scrapped
                        FROM source_document sd
                        JOIN news_article na ON na.document_id = sd.document_id
                        WHERE sd.document_id = :newsId
                          AND sd.document_type = 'NEWS'
                          AND sd.published_at IS NOT NULL
                          AND EXISTS (
                              SELECT 1
                              FROM company_document cd
                              JOIN company c ON c.company_id = cd.company_id
                              WHERE cd.document_id = sd.document_id
                                AND cd.is_service_visible = TRUE
                                AND c.status = 'ACTIVE'
                          )
                        """,
                new MapSqlParameterSource()
                        .addValue("newsId", newsId)
                        .addValue("userId", userId),
                (rs, rowNum) -> new NewsDetailRow(
                        rs.getObject("news_id", UUID.class),
                        rs.getString("title"),
                        rs.getString("summary"),
                        rs.getString("publisher"),
                        rs.getString("author"),
                        rs.getString("original_url"),
                        rs.getTimestamp("published_at").toInstant(),
                        rs.getBoolean("scrapped")))
                .stream().findFirst();
    }

    public List<EvidenceRow> findEvidence(UUID newsId) {
        return jdbcTemplate.query("""
                        SELECT de.sentence_text, de.confidence
                        FROM document_evidence de
                        JOIN company_document cd
                          ON cd.document_id = de.document_id
                         AND cd.company_id = de.company_id
                        JOIN company c ON c.company_id = cd.company_id
                        WHERE de.document_id = :newsId
                          AND cd.is_service_visible = TRUE
                          AND c.status = 'ACTIVE'
                        ORDER BY de.sentence_order, de.evidence_id
                        """,
                Map.of("newsId", newsId),
                (rs, rowNum) -> new EvidenceRow(
                        rs.getString("sentence_text"), rs.getBigDecimal("confidence")));
    }

    public List<NewsRow> search(
            NewsSearchCondition condition,
            Instant fromInclusive,
            Instant toExclusive,
            Instant cursorPublishedAt,
            UUID cursorNewsId,
            Long userId
    ) {
        String sql = """
                SELECT sd.document_id AS news_id, sd.title, sd.summary, na.publisher,
                       sd.original_url, sd.published_at, representative.sentiment,
                       EXISTS (
                           SELECT 1 FROM user_scrap us
                           WHERE us.document_id = sd.document_id
                             AND us.user_id = CAST(:userId AS BIGINT)
                       ) AS scrapped
                FROM source_document sd
                JOIN news_article na ON na.document_id = sd.document_id
                JOIN LATERAL (
                    SELECT cd.sentiment
                    FROM company_document cd
                    JOIN company c ON c.company_id = cd.company_id
                    WHERE cd.document_id = sd.document_id
                      AND cd.is_service_visible = TRUE
                      AND c.status = 'ACTIVE'
                      AND (CAST(:companyId AS UUID) IS NULL OR cd.company_id = CAST(:companyId AS UUID))
                      AND (CAST(:sentiment AS TEXT) IS NULL OR cd.sentiment = CAST(:sentiment AS TEXT))
                      AND (CAST(:industryId AS UUID) IS NULL OR EXISTS (
                          SELECT 1 FROM company_industry ci
                          WHERE ci.company_id = cd.company_id
                            AND ci.industry_id = CAST(:industryId AS UUID)
                      ))
                    ORDER BY cd.relevance_score DESC NULLS LAST, cd.company_id
                    LIMIT 1
                ) representative ON TRUE
                WHERE sd.document_type = 'NEWS'
                  AND sd.published_at IS NOT NULL
                  AND (CAST(:keyword AS TEXT) IS NULL
                       OR lower(sd.title) LIKE CAST(:keywordPattern AS TEXT) ESCAPE '\\')
                  AND (CAST(:fromInclusive AS TIMESTAMPTZ) IS NULL
                       OR sd.published_at >= CAST(:fromInclusive AS TIMESTAMPTZ))
                  AND (CAST(:toExclusive AS TIMESTAMPTZ) IS NULL
                       OR sd.published_at < CAST(:toExclusive AS TIMESTAMPTZ))
                  AND (CAST(:cursorPublishedAt AS TIMESTAMPTZ) IS NULL OR
                       (sd.published_at, sd.document_id) <
                       (CAST(:cursorPublishedAt AS TIMESTAMPTZ), CAST(:cursorNewsId AS UUID)))
                ORDER BY sd.published_at DESC, sd.document_id DESC
                LIMIT :limit
                """;
        MapSqlParameterSource params = new MapSqlParameterSource()
                .addValue("keyword", condition.keyword())
                .addValue("keywordPattern", likePattern(condition.keyword()))
                .addValue("companyId", condition.companyId())
                .addValue("industryId", condition.industryId())
                .addValue("sentiment", condition.sentiment())
                .addValue("userId", userId)
                .addValue("fromInclusive", timestamp(fromInclusive))
                .addValue("toExclusive", timestamp(toExclusive))
                .addValue("cursorPublishedAt", timestamp(cursorPublishedAt))
                .addValue("cursorNewsId", cursorNewsId)
                .addValue("limit", condition.size() + 1);
        return jdbcTemplate.query(sql, params, this::mapNews);
    }

    private NewsRow mapNews(ResultSet rs, int rowNum) throws SQLException {
        return new NewsRow(
                rs.getObject("news_id", UUID.class), rs.getString("title"),
                rs.getString("summary"), rs.getString("publisher"), rs.getString("original_url"),
                rs.getTimestamp("published_at").toInstant(), rs.getString("sentiment"),
                rs.getBoolean("scrapped"));
    }

    private RelatedCompanyRow mapRelatedCompany(ResultSet rs, int rowNum) throws SQLException {
        return new RelatedCompanyRow(
                rs.getObject("news_id", UUID.class), rs.getObject("company_id", UUID.class),
                rs.getString("name"), rs.getString("sentiment"),
                rs.getBigDecimal("relevance_score"), rs.getBigDecimal("impact_score"));
    }

    private String likePattern(String keyword) {
        if (keyword == null) {
            return null;
        }
        String escaped = keyword.toLowerCase(java.util.Locale.ROOT)
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_");
        return "%" + escaped + "%";
    }

    private Timestamp timestamp(Instant value) {
        return value == null ? null : Timestamp.from(value);
    }

    public record NewsRow(
            UUID newsId, String title, String summary, String publisher, String originalUrl,
            Instant publishedAt, String sentiment, boolean scrapped
    ) {
    }

    public record NewsDetailRow(
            UUID newsId, String title, String summary, String publisher, String author,
            String originalUrl, Instant publishedAt, boolean scrapped
    ) {
    }

    public record RelatedCompanyRow(
            UUID newsId, UUID companyId, String name, String sentiment,
            BigDecimal relevanceScore, BigDecimal impactScore
    ) {
    }

    public record EvidenceRow(String sentence, BigDecimal confidence) {
    }
}
