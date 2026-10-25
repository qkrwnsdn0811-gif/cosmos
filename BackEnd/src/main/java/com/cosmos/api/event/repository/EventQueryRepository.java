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
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

/** NEXUS 이벤트 조회(V10·V12 테이블). 쓰기는 파이프라인(nexus/)이 하고 여기서는 읽기만 한다. */
@Repository
public class EventQueryRepository {

    private static final String EVENT_COLUMNS = """
            e.event_id, e.document_id AS news_id, sd.title, sd.summary, na.publisher, sd.original_url,
            e.dateline, e.event_trading_date, e.value_chain, e.categories, e.event_type,
            (SELECT count(*) FROM event_company_impact i
              WHERE i.event_id = e.event_id AND i.basis = 'INFERRED' AND i.status = 'OK') AS inferred_count,
            (SELECT count(*) FROM event_company_impact i
              WHERE i.event_id = e.event_id AND i.basis = 'INFERRED' AND i.status = 'HOLD') AS hold_count
            """;

    /** 기업 과거 뉴스 조회에서 OK 영향이 이만큼 이상이면 최신 이벤트부터 훑고, 적으면 기업 영향부터 모은다. */
    static final int DENSE_COMPANY_IMPACTS = 2000;

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
                  AND (CAST(:newsType AS VARCHAR) IS NULL OR e.event_type = :newsType)
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
                        .addValue("newsType", condition.newsType())
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

    /**
     * 이 기업이 OK 영향으로 들어간 이벤트, 최신순(F13). basis는 이 기업의 근거 구분이다.
     *
     * <p>플래너는 기업별 OK 비율을 몰라(인덱스가 company_id, basis뿐) 한 가지 계획만 고르는데, 최신 이벤트부터 훑는 계획은
     * OK 영향이 드문 기업(추정 HOLD만 많은 기업, 없는 기업)에서 이벤트 전체를 훑어 수백 ms~수 초가 걸리고, 기업 영향부터
     * 모으는 계획은 삼성전자처럼 수만 건인 기업에서 느리다. 그래서 OK 영향 수를 상한까지만 세어 계획을 나눈다.
     */
    public List<CompanyEventRow> findCompanyEvents(UUID companyId, TimeIdCursor cursor, int limit) {
        Integer okImpacts = jdbcTemplate.queryForObject("""
                SELECT count(*) FROM (
                    SELECT 1 FROM event_company_impact
                    WHERE company_id = :companyId AND status = 'OK'
                    LIMIT :dense
                ) capped
                """, new MapSqlParameterSource().addValue("companyId", companyId)
                        .addValue("dense", DENSE_COMPANY_IMPACTS), Integer.class);
        return findCompanyEvents(companyId, cursor, limit, okImpacts != null && okImpacts >= DENSE_COMPANY_IMPACTS);
    }

    /** 계획을 직접 고르는 판. scanByDateline이면 최신 이벤트부터 훑고, 아니면 기업 영향부터 모은다. 결과는 같다. */
    public List<CompanyEventRow> findCompanyEvents(UUID companyId, TimeIdCursor cursor, int limit,
            boolean scanByDateline) {
        MapSqlParameterSource params = new MapSqlParameterSource()
                .addValue("companyId", companyId)
                .addValue("cursorAt", cursor == null ? null : Timestamp.from(cursor.at()))
                .addValue("cursorId", cursor == null ? null : cursor.id())
                .addValue("limit", limit);
        // OFFSET 0 은 서브쿼리를 펼치지 못하게 막아, 기업 영향(인덱스)부터 모은 뒤 이벤트를 붙이게 한다
        String from = scanByDateline
                ? "FROM event_company_impact ci\n"
                : """
                FROM (SELECT event_id, company_id, basis, status FROM event_company_impact
                      WHERE company_id = :companyId AND status = 'OK' OFFSET 0) ci
                """;
        return jdbcTemplate.query("SELECT " + EVENT_COLUMNS + ", ci.basis AS company_basis\n" + from + """
                JOIN news_event e ON e.event_id = ci.event_id
                JOIN source_document sd ON sd.document_id = e.document_id
                LEFT JOIN news_article na ON na.document_id = e.document_id
                WHERE ci.company_id = :companyId AND ci.status = 'OK' AND e.status = 'ACTIVE'
                  AND (CAST(:cursorAt AS TIMESTAMPTZ) IS NULL
                       OR (e.dateline, e.event_id) < (CAST(:cursorAt AS TIMESTAMPTZ), CAST(:cursorId AS UUID)))
                ORDER BY e.dateline DESC, e.event_id DESC
                LIMIT :limit
                """,
                params,
                (rs, rowNum) -> new CompanyEventRow(mapEvent(rs, rowNum), rs.getString("company_basis")));
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
                       i.hold_reason, i.impact_direction, i.impact_magnitude, i.confidence,
                       i.path::text AS path,
                       (SELECT ind.name FROM company_industry ci JOIN industry ind ON ind.industry_id = ci.industry_id
                        WHERE ci.company_id = i.company_id AND ci.is_primary LIMIT 1) AS industry
                FROM event_company_impact i
                JOIN company c ON c.company_id = i.company_id
                WHERE i.event_id = :eventId
                ORDER BY CASE i.basis WHEN 'REPORTED' THEN 0 ELSE 1 END, i.propagation_score DESC, c.name
                """, new MapSqlParameterSource("eventId", eventId), (rs, rowNum) -> new ImpactRow(
                rs.getObject("company_id", UUID.class), rs.getString("name"), rs.getString("stock_code"),
                rs.getString("market"), rs.getString("basis"), rs.getInt("hop"), rs.getBigDecimal("propagation_score"),
                rs.getString("status"), rs.getString("hold_reason"), rs.getString("impact_direction"),
                rs.getBigDecimal("impact_magnitude"), rs.getBigDecimal("confidence"), rs.getString("path"),
                rs.getString("industry")));
    }

    /** 이벤트의 모든 기업·모든 기간(1·3·5·20거래일) 초과수익률. 기간을 거르지 않는다. */
    public List<AbnormalReturnRow> findAbnormalReturns(UUID eventId) {
        return jdbcTemplate.query("""
                SELECT event_id, company_id, window_days, benchmark, stock_return, benchmark_return, abnormal_return
                FROM event_abnormal_return
                WHERE event_id = :eventId
                ORDER BY company_id, window_days
                """, new MapSqlParameterSource("eventId", eventId), this::mapAbnormalReturn);
    }

    /** 기업 한 곳의 여러 이벤트 초과수익률(기업 과거 뉴스 한 페이지 분량을 한 번에). */
    public List<AbnormalReturnRow> findCompanyAbnormalReturns(UUID companyId, List<UUID> eventIds) {
        if (eventIds.isEmpty()) {
            return List.of();
        }
        return jdbcTemplate.query("""
                SELECT event_id, company_id, window_days, benchmark, stock_return, benchmark_return, abnormal_return
                FROM event_abnormal_return
                WHERE company_id = :companyId AND event_id IN (:eventIds)
                ORDER BY event_id, window_days
                """, new MapSqlParameterSource().addValue("companyId", companyId).addValue("eventIds", eventIds),
                this::mapAbnormalReturn);
    }

    /**
     * 이벤트별 · 투자 기간별 · 딱지별 기업 수(목록 한 페이지 분량을 한 번에). 화면의 '영향 받을 종목'과 같게 OK 영향만 센다 —
     * 연결이 약해 보류된 기업(영향 HOLD)은 접어 두는 목록이라 세지 않는다.
     */
    public List<SignalCountRow> findSignalCounts(List<UUID> eventIds) {
        if (eventIds.isEmpty()) {
            return List.of();
        }
        return jdbcTemplate.query("""
                SELECT s.event_id, s.horizon, s.label, count(*) AS cnt
                FROM event_company_signal s
                JOIN event_company_impact i ON i.event_id = s.event_id AND i.company_id = s.company_id AND i.status = 'OK'
                WHERE s.event_id IN (:eventIds)
                GROUP BY s.event_id, s.horizon, s.label
                """, new MapSqlParameterSource("eventIds", eventIds), (rs, rowNum) -> new SignalCountRow(
                rs.getObject("event_id", UUID.class), rs.getString("horizon"), rs.getString("label"),
                rs.getInt("cnt")));
    }

    /** 이벤트 한 건의 모든 기업 방향 딱지. 기업마다 SHORT, LONG 순이다. */
    public List<SignalRow> findSignals(UUID eventId) {
        return jdbcTemplate.query("""
                SELECT event_id, company_id, horizon, position, label, hold_reason, case_scope, case_count,
                       same_share, mean_ar, down_share
                FROM event_company_signal
                WHERE event_id = :eventId
                ORDER BY company_id, CASE horizon WHEN 'SHORT' THEN 0 ELSE 1 END
                """, new MapSqlParameterSource("eventId", eventId), this::mapSignal);
    }

    /** 기업 한 곳의 여러 이벤트 방향 딱지. 이벤트마다 SHORT, LONG 순이다. */
    public List<SignalRow> findCompanySignals(UUID companyId, List<UUID> eventIds) {
        if (eventIds.isEmpty()) {
            return List.of();
        }
        return jdbcTemplate.query("""
                SELECT event_id, company_id, horizon, position, label, hold_reason, case_scope, case_count,
                       same_share, mean_ar, down_share
                FROM event_company_signal
                WHERE company_id = :companyId AND event_id IN (:eventIds)
                ORDER BY event_id, CASE horizon WHEN 'SHORT' THEN 0 ELSE 1 END
                """, new MapSqlParameterSource().addValue("companyId", companyId).addValue("eventIds", eventIds),
                this::mapSignal);
    }

    /** 이 이벤트의 영향 기업(OK·HOLD 모두)이면 그 기업의 시장. 영향 기업이 아니면 빈 값. */
    public Optional<ImpactCompanyRow> findImpactCompany(UUID eventId, UUID companyId) {
        return jdbcTemplate.query("""
                SELECT c.company_id, c.market
                FROM event_company_impact i
                JOIN company c ON c.company_id = i.company_id
                WHERE i.event_id = :eventId AND i.company_id = :companyId
                """, new MapSqlParameterSource().addValue("eventId", eventId).addValue("companyId", companyId),
                (rs, rowNum) -> new ImpactCompanyRow(rs.getObject("company_id", UUID.class), rs.getString("market")))
                .stream().findFirst();
    }

    /**
     * 지수 거래일 달력 조각: tradingDate 앞 거래일 최대 {@code before}개와 tradingDate 이후(당일 포함) 최대 {@code from}개,
     * 오래된 순. 차트의 거래일 기준이다.
     */
    public List<IndexCloseRow> findIndexCalendar(String indexCode, LocalDate tradingDate, int before, int from) {
        return jdbcTemplate.query("""
                SELECT trading_date, close_price FROM (
                    (SELECT trading_date, close_price FROM market_index_price
                     WHERE index_code = :indexCode AND trading_date < :tradingDate
                     ORDER BY trading_date DESC LIMIT :before)
                    UNION ALL
                    (SELECT trading_date, close_price FROM market_index_price
                     WHERE index_code = :indexCode AND trading_date >= :tradingDate
                     ORDER BY trading_date LIMIT :from)
                ) calendar
                ORDER BY trading_date
                """, new MapSqlParameterSource()
                        .addValue("indexCode", indexCode).addValue("tradingDate", tradingDate)
                        .addValue("before", before).addValue("from", from),
                (rs, rowNum) -> new IndexCloseRow(rs.getObject("trading_date", LocalDate.class),
                        rs.getBigDecimal("close_price")));
    }

    /** 기업 일봉 종가, 거래일 → 종가. stock_price_history.trading_at은 거래일의 UTC 자정이다. */
    public Map<LocalDate, BigDecimal> findDailyCloses(UUID companyId, LocalDate from, LocalDate to) {
        Map<LocalDate, BigDecimal> result = new HashMap<>();
        jdbcTemplate.query("""
                SELECT CAST(trading_at AT TIME ZONE 'UTC' AS DATE) AS trading_date, close_price
                FROM stock_price_history
                WHERE company_id = :companyId AND interval_type = '1D'
                  AND trading_at >= :fromAt AND trading_at < :toAt
                """, new MapSqlParameterSource()
                        .addValue("companyId", companyId)
                        .addValue("fromAt", Timestamp.from(from.atStartOfDay(ZoneOffset.UTC).toInstant()))
                        .addValue("toAt", Timestamp.from(to.plusDays(1).atStartOfDay(ZoneOffset.UTC).toInstant())),
                (ResultSet rs) -> {
                    result.put(rs.getObject("trading_date", LocalDate.class), rs.getBigDecimal("close_price"));
                });
        return result;
    }

    /** 적중률 표. SHORT 먼저, 기간 안에서 전체 유형(news_type NULL) 줄 다음 딱지 수 내림차순. */
    public List<BacktestRow> findBacktest(String valueChain) {
        return jdbcTemplate.query("""
                SELECT horizon, news_type, case_count, labeled_count, hit_count, up_labels, down_labels,
                       base_down_share, train_end, test_start, test_end
                FROM signal_backtest
                WHERE value_chain = :valueChain
                ORDER BY CASE horizon WHEN 'SHORT' THEN 0 ELSE 1 END, news_type IS NOT NULL, labeled_count DESC,
                         news_type
                """, new MapSqlParameterSource("valueChain", valueChain), (rs, rowNum) -> new BacktestRow(
                rs.getString("horizon"), rs.getString("news_type"), rs.getInt("case_count"),
                rs.getInt("labeled_count"), rs.getInt("hit_count"), rs.getInt("up_labels"), rs.getInt("down_labels"),
                rs.getBigDecimal("base_down_share"), rs.getObject("train_end", LocalDate.class),
                rs.getObject("test_start", LocalDate.class), rs.getObject("test_end", LocalDate.class)));
    }

    /** 예상 변동폭 검증 표. SHORT 먼저, 기간 안에서 SMALL · MID · LARGE 순. */
    public List<MoveBacktestRow> findMoveBacktest(String valueChain) {
        return jdbcTemplate.query("""
                SELECT horizon, grade, case_count, predicted_move, realized_move
                FROM move_backtest
                WHERE value_chain = :valueChain
                ORDER BY CASE horizon WHEN 'SHORT' THEN 0 ELSE 1 END,
                         CASE grade WHEN 'SMALL' THEN 0 WHEN 'MID' THEN 1 ELSE 2 END
                """, new MapSqlParameterSource("valueChain", valueChain), (rs, rowNum) -> new MoveBacktestRow(
                rs.getString("horizon"), rs.getString("grade"), rs.getInt("case_count"),
                rs.getBigDecimal("predicted_move"), rs.getBigDecimal("realized_move")));
    }

    /** 기업들의 예상 변동폭. 기업마다 SHORT, LONG 순이다. */
    public List<ExpectedMoveRow> findExpectedMoves(String valueChain, Collection<UUID> companyIds) {
        if (companyIds.isEmpty()) {
            return List.of();
        }
        return jdbcTemplate.query("""
                SELECT company_id, horizon, expected_move, case_count, grade
                FROM company_expected_move
                WHERE value_chain = :valueChain AND company_id IN (:companyIds)
                ORDER BY company_id, CASE horizon WHEN 'SHORT' THEN 0 ELSE 1 END
                """, new MapSqlParameterSource().addValue("valueChain", valueChain).addValue("companyIds", companyIds),
                (rs, rowNum) -> new ExpectedMoveRow(rs.getObject("company_id", UUID.class), rs.getString("horizon"),
                        rs.getBigDecimal("expected_move"), rs.getInt("case_count"), rs.getString("grade")));
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

    /**
     * 두 기업 사이 관계의 근거 조각. 이벤트 시각(asOf) 이전 기사를 먼저, 그 안에서는 이벤트와 가까운 순으로 두고,
     * 이벤트 뒤 기사는 그다음에 이벤트와 가까운 순으로 둔다(관계 그래프가 전체 기간 기사로 만들어져 뒤 기사만 있는 관계도 있다).
     * 무방향 관계(협력·경쟁)는 반대 방향으로 저장된 조각도 찾는다.
     */
    public List<EvidenceSnippetResponse> findSnippets(UUID sourceId, UUID targetId, String relationshipType,
            Instant asOf, int limit) {
        return jdbcTemplate.query("""
                SELECT s.bigkinds_news_id, s.title, s.provider, s.published_at, s.original_url, s.snippet, s.confidence
                FROM relation_evidence_snippet s
                JOIN relationship_type t ON t.relationship_type_id = s.relationship_type_id
                WHERE t.code = :type
                  AND ((s.source_company_id = :src AND s.target_company_id = :dst)
                       OR (t.directionality = 'UNDIRECTED' AND s.source_company_id = :dst AND s.target_company_id = :src))
                ORDER BY s.published_at > :asOf,
                         CASE WHEN s.published_at <= :asOf THEN s.published_at END DESC,
                         s.published_at
                LIMIT :limit
                """, new MapSqlParameterSource()
                        .addValue("type", relationshipType).addValue("src", sourceId).addValue("dst", targetId)
                        .addValue("asOf", Timestamp.from(asOf)).addValue("limit", limit),
                (rs, rowNum) -> new EvidenceSnippetResponse(
                        rs.getString("bigkinds_news_id"), rs.getString("title"), rs.getString("provider"),
                        rs.getTimestamp("published_at").toInstant(), rs.getString("original_url"),
                        rs.getString("snippet"), rs.getBigDecimal("confidence")));
    }

    private AbnormalReturnRow mapAbnormalReturn(ResultSet rs, int rowNum) throws SQLException {
        return new AbnormalReturnRow(
                rs.getObject("event_id", UUID.class), rs.getObject("company_id", UUID.class),
                rs.getInt("window_days"), rs.getString("benchmark"), rs.getBigDecimal("stock_return"),
                rs.getBigDecimal("benchmark_return"), rs.getBigDecimal("abnormal_return"));
    }

    private SignalRow mapSignal(ResultSet rs, int rowNum) throws SQLException {
        return new SignalRow(
                rs.getObject("event_id", UUID.class), rs.getObject("company_id", UUID.class),
                rs.getString("horizon"), rs.getString("position"), rs.getString("label"),
                rs.getString("hold_reason"), rs.getString("case_scope"), rs.getInt("case_count"),
                rs.getBigDecimal("same_share"), rs.getBigDecimal("mean_ar"), rs.getBigDecimal("down_share"));
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
                rs.getInt("hold_count"),
                rs.getString("event_type"));
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
            int holdCount,
            String newsType
    ) {
    }

    public record CompanyEventRow(EventRow event, String basis) {
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
            BigDecimal impactMagnitude,
            BigDecimal confidence,
            String pathJson,
            String industry
    ) {
    }

    public record AbnormalReturnRow(
            UUID eventId,
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

    public record SignalCountRow(UUID eventId, String horizon, String label, int count) {
    }

    public record SignalRow(
            UUID eventId,
            UUID companyId,
            String horizon,
            String position,
            String label,
            String holdReason,
            String caseScope,
            int caseCount,
            BigDecimal sameShare,
            BigDecimal meanAr,
            BigDecimal downShare
    ) {
    }

    public record ImpactCompanyRow(UUID companyId, String market) {
    }

    public record IndexCloseRow(LocalDate tradingDate, BigDecimal close) {
    }

    public record BacktestRow(
            String horizon,
            String newsType,
            int caseCount,
            int labeledCount,
            int hitCount,
            int upLabels,
            int downLabels,
            BigDecimal baseDownShare,
            LocalDate trainEnd,
            LocalDate testStart,
            LocalDate testEnd
    ) {
    }

    public record ExpectedMoveRow(UUID companyId, String horizon, BigDecimal expectedMove, int caseCount, String grade) {
    }

    public record MoveBacktestRow(String horizon, String grade, int caseCount, BigDecimal predictedMove,
            BigDecimal realizedMove) {
    }
}
