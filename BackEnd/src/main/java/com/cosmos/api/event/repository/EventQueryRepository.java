package com.cosmos.api.event.repository;

import com.cosmos.api.event.dto.EventCompanyRefResponse;
import com.cosmos.api.global.cursor.TimeIdCursor;
import java.sql.Array;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDate;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

/** NEXUS 이벤트 조회(V10 테이블). 쓰기는 파이프라인(nexus/)이 하고 여기서는 읽기만 한다. */
@Repository
public class EventQueryRepository {

    private static final String EVENT_COLUMNS = """
            e.event_id, e.document_id AS news_id, sd.title, sd.summary, na.publisher, sd.original_url,
            e.dateline, e.event_trading_date, e.value_chain, e.categories,
            (SELECT count(*) FROM event_company_impact i
              WHERE i.event_id = e.event_id AND i.basis = 'INFERRED' AND i.status = 'OK') AS inferred_count,
            (SELECT count(*) FROM event_company_impact i
              WHERE i.event_id = e.event_id AND i.basis = 'INFERRED' AND i.status = 'HOLD') AS hold_count
            """;

    private final NamedParameterJdbcTemplate jdbcTemplate;

    public EventQueryRepository(NamedParameterJdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public List<EventRow> findEvents(EventSearchCondition condition, TimeIdCursor cursor, int limit) {
        return jdbcTemplate.query("SELECT " + EVENT_COLUMNS + """
                FROM news_event e
                JOIN source_document sd ON sd.document_id = e.document_id
                LEFT JOIN news_article na ON na.document_id = e.document_id
                WHERE e.status = 'ACTIVE'
                  AND (CAST(:valueChain AS VARCHAR) IS NULL OR e.value_chain = :valueChain)
                  AND (CAST(:companyId AS UUID) IS NULL OR EXISTS (
                        SELECT 1 FROM event_company_impact c
                        WHERE c.event_id = e.event_id AND c.company_id = CAST(:companyId AS UUID) AND c.status = 'OK'))
                  AND (:hasInferred = FALSE OR EXISTS (
                        SELECT 1 FROM event_company_impact c
                        WHERE c.event_id = e.event_id AND c.basis = 'INFERRED' AND c.status = 'OK'))
                  AND (CAST(:cursorAt AS TIMESTAMPTZ) IS NULL
                       OR (e.dateline, e.event_id) < (CAST(:cursorAt AS TIMESTAMPTZ), CAST(:cursorId AS UUID)))
                ORDER BY e.dateline DESC, e.event_id DESC
                LIMIT :limit
                """,
                new MapSqlParameterSource()
                        .addValue("valueChain", condition.valueChain())
                        .addValue("companyId", condition.companyId())
                        .addValue("hasInferred", condition.hasInferred())
                        .addValue("cursorAt", cursor == null ? null : Timestamp.from(cursor.at()))
                        .addValue("cursorId", cursor == null ? null : cursor.id())
                        .addValue("limit", limit),
                this::mapEvent);
    }

    public Optional<EventRow> findEvent(UUID eventId) {
        return jdbcTemplate.query("SELECT " + EVENT_COLUMNS + """
                FROM news_event e
                JOIN source_document sd ON sd.document_id = e.document_id
                LEFT JOIN news_article na ON na.document_id = e.document_id
                WHERE e.event_id = :eventId
                """, new MapSqlParameterSource("eventId", eventId), this::mapEvent).stream().findFirst();
    }

    public Map<UUID, List<EventCompanyRefResponse>> findReportedCompanies(List<UUID> eventIds) {
        Map<UUID, List<EventCompanyRefResponse>> result = new LinkedHashMap<>();
        if (eventIds.isEmpty()) {
            return result;
        }
        jdbcTemplate.query("""
                SELECT i.event_id, c.company_id, c.name, c.stock_code, c.market
                FROM event_company_impact i
                JOIN company c ON c.company_id = i.company_id
                WHERE i.event_id IN (:eventIds) AND i.basis = 'REPORTED'
                ORDER BY i.propagation_score DESC, c.name
                """, new MapSqlParameterSource("eventIds", eventIds), (ResultSet rs) -> {
            result.computeIfAbsent(rs.getObject("event_id", UUID.class), k -> new java.util.ArrayList<>())
                    .add(new EventCompanyRefResponse(rs.getObject("company_id", UUID.class), rs.getString("name"),
                            rs.getString("stock_code"), rs.getString("market")));
        });
        return result;
    }

    private EventRow mapEvent(ResultSet rs, int rowNum) throws SQLException {
        Array categories = rs.getArray("categories");
        return new EventRow(
                rs.getObject("event_id", UUID.class),
                rs.getObject("news_id", UUID.class),
                rs.getString("title"),
                rs.getString("summary"),
                rs.getString("publisher"),
                rs.getString("original_url"),
                rs.getTimestamp("dateline").toInstant(),
                rs.getObject("event_trading_date", LocalDate.class),
                rs.getString("value_chain"),
                categories == null ? List.of() : Arrays.asList((String[]) categories.getArray()),
                rs.getInt("inferred_count"),
                rs.getInt("hold_count"));
    }

    public record EventRow(
            UUID eventId,
            UUID newsId,
            String title,
            String summary,
            String publisher,
            String originalUrl,
            Instant dateline,
            LocalDate tradingDate,
            String valueChain,
            List<String> categories,
            int inferredCount,
            int holdCount
    ) {
    }
}
