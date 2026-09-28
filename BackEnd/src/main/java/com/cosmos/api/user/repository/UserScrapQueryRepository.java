package com.cosmos.api.user.repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

/**
 * 내 스크랩 목록 조회. 스크랩 최신순(스크랩 시각 내림차순, 같으면 뉴스 번호 내림차순).
 * 뉴스 쪽은 JPA 엔티티가 없어 SQL로 user_scrap + source_document + news_article 를 한 번에 읽는다.
 */
@Repository
public class UserScrapQueryRepository {

    private final NamedParameterJdbcTemplate jdbcTemplate;

    public UserScrapQueryRepository(NamedParameterJdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /**
     * @param cursorScrappedAt 커서의 스크랩 시각. 첫 페이지면 null
     * @param cursorNewsId     커서의 뉴스 번호. 첫 페이지면 null
     * @param limit            읽을 최대 개수 (보통 size + 1)
     */
    public List<ScrapRow> findPage(Long userId, Instant cursorScrappedAt, UUID cursorNewsId, int limit) {
        String sql = """
                SELECT us.document_id AS news_id, sd.title, sd.summary, na.publisher,
                       sd.original_url, sd.published_at, us.created_at AS scrapped_at
                FROM user_scrap us
                JOIN source_document sd ON sd.document_id = us.document_id
                JOIN news_article na ON na.document_id = us.document_id
                WHERE us.user_id = :userId
                  AND (CAST(:cursorScrappedAt AS TIMESTAMPTZ) IS NULL OR
                       (us.created_at, us.document_id) <
                       (CAST(:cursorScrappedAt AS TIMESTAMPTZ), CAST(:cursorNewsId AS UUID)))
                ORDER BY us.created_at DESC, us.document_id DESC
                LIMIT :limit
                """;
        MapSqlParameterSource params = new MapSqlParameterSource()
                .addValue("userId", userId)
                .addValue("cursorScrappedAt", cursorScrappedAt == null ? null : Timestamp.from(cursorScrappedAt))
                .addValue("cursorNewsId", cursorNewsId)
                .addValue("limit", limit);
        return jdbcTemplate.query(sql, params, (rs, rowNum) -> new ScrapRow(
                rs.getObject("news_id", UUID.class),
                rs.getString("title"),
                rs.getString("summary"),
                rs.getString("publisher"),
                rs.getString("original_url"),
                rs.getTimestamp("published_at") == null ? null : rs.getTimestamp("published_at").toInstant(),
                rs.getTimestamp("scrapped_at").toInstant()));
    }

    public record ScrapRow(
            UUID newsId,
            String title,
            String summary,
            String publisher,
            String originalUrl,
            Instant publishedAt,
            Instant scrappedAt
    ) {
    }
}
