package com.cosmos.api.event.repository;

import com.cosmos.api.event.dto.EventCompanyRefResponse;
import com.cosmos.api.event.dto.EvidenceSnippetResponse;
import com.cosmos.api.global.cursor.TimeIdCursor;
import java.math.BigDecimal;
import java.sql.Array;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
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
                WHERE e.event_id = :eventId AND e.status = 'ACTIVE'
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
            result.computeIfAbsent(rs.getObject("event_id", UUID.class), k -> new ArrayList<>())
                    .add(new EventCompanyRefResponse(rs.getObject("company_id", UUID.class), rs.getString("name"),
                            rs.getString("stock_code"), rs.getString("market")));
        });
        return result;
    }

    public List<ImpactRow> findImpacts(UUID eventId) {
        return jdbcTemplate.query("""
                SELECT i.company_id, c.name, c.stock_code, c.market, i.basis, i.hop, i.propagation_score, i.status,
                       i.hold_reason, i.impact_direction, i.confidence, i.path::text AS path
                FROM event_company_impact i
                JOIN company c ON c.company_id = i.company_id
                WHERE i.event_id = :eventId
                ORDER BY CASE i.basis WHEN 'REPORTED' THEN 0 ELSE 1 END, i.propagation_score DESC, c.name
                """, new MapSqlParameterSource("eventId", eventId), (rs, rowNum) -> new ImpactRow(
                rs.getObject("company_id", UUID.class), rs.getString("name"), rs.getString("stock_code"),
                rs.getString("market"), rs.getString("basis"), rs.getInt("hop"), rs.getBigDecimal("propagation_score"),
                rs.getString("status"), rs.getString("hold_reason"), rs.getString("impact_direction"),
                rs.getBigDecimal("confidence"), rs.getString("path")));
    }

    public List<AbnormalReturnRow> findAbnormalReturns(UUID eventId) {
        return jdbcTemplate.query("""
                SELECT company_id, window_days, benchmark, stock_return, benchmark_return, abnormal_return
                FROM event_abnormal_return
                WHERE event_id = :eventId
                ORDER BY company_id, window_days
                """, new MapSqlParameterSource("eventId", eventId), (rs, rowNum) -> new AbnormalReturnRow(
                rs.getObject("company_id", UUID.class), rs.getInt("window_days"), rs.getString("benchmark"),
                rs.getBigDecimal("stock_return"), rs.getBigDecimal("benchmark_return"),
                rs.getBigDecimal("abnormal_return")));
    }

    /** 파이프라인 경로의 기업 key("MARKET:TICKER") → 기업. 마스터에 없는 key는 결과에 없다. */
    public Map<String, CompanyKeyRow> findCompaniesByKeys(Collection<String> keys) {
        Map<String, CompanyKeyRow> result = new LinkedHashMap<>();
        if (keys.isEmpty()) {
            return result;
        }
        jdbcTemplate.query("""
                SELECT company_id, name, market || ':' || stock_code AS company_key
                FROM company
                WHERE market || ':' || stock_code IN (:keys)
                """, new MapSqlParameterSource("keys", keys), (ResultSet rs) -> {
            result.put(rs.getString("company_key"),
                    new CompanyKeyRow(rs.getObject("company_id", UUID.class), rs.getString("name")));
        });
        return result;
    }

    /** 두 기업 사이 관계의 근거 조각, 최신순. 무방향 관계(협력·경쟁)는 반대 방향으로 저장된 조각도 찾는다. */
    public List<EvidenceSnippetResponse> findSnippets(UUID sourceId, UUID targetId, String relationshipType, int limit) {
        return jdbcTemplate.query("""
                SELECT s.bigkinds_news_id, s.title, s.provider, s.published_at, s.original_url, s.snippet, s.confidence
                FROM relation_evidence_snippet s
                JOIN relationship_type t ON t.relationship_type_id = s.relationship_type_id
                WHERE t.code = :type
                  AND ((s.source_company_id = :src AND s.target_company_id = :dst)
                       OR (t.directionality = 'UNDIRECTED' AND s.source_company_id = :dst AND s.target_company_id = :src))
                ORDER BY s.published_at DESC
                LIMIT :limit
                """, new MapSqlParameterSource()
                        .addValue("type", relationshipType).addValue("src", sourceId).addValue("dst", targetId)
                        .addValue("limit", limit),
                (rs, rowNum) -> new EvidenceSnippetResponse(
                        rs.getString("bigkinds_news_id"), rs.getString("title"), rs.getString("provider"),
                        rs.getTimestamp("published_at").toInstant(), rs.getString("original_url"),
                        rs.getString("snippet"), rs.getBigDecimal("confidence")));
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

    public record ImpactRow(
            UUID companyId,
            String name,
            String stockCode,
            String market,
            String basis,
            int hop,
            BigDecimal score,
            String status,
            String holdReason,
            String impactDirection,
            BigDecimal confidence,
            String pathJson
    ) {
    }

    public record AbnormalReturnRow(
            UUID companyId,
            int windowDays,
            String benchmark,
            BigDecimal stockReturn,
            BigDecimal benchmarkReturn,
            BigDecimal abnormalReturn
    ) {
    }

    public record CompanyKeyRow(UUID companyId, String name) {
    }
}
