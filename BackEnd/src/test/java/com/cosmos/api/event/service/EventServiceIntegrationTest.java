package com.cosmos.api.event.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.cosmos.api.event.dto.AbnormalReturnResponse;
import com.cosmos.api.event.dto.EventCompanyRefResponse;
import com.cosmos.api.event.dto.EventMapResponse;
import com.cosmos.api.event.dto.EventNodeResponse;
import com.cosmos.api.event.dto.EventSummaryResponse;
import com.cosmos.api.event.dto.EvidenceSnippetResponse;
import com.cosmos.api.event.error.EventErrorCode;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import com.cosmos.api.global.response.CursorPageResponse;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDate;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

/** 시드는 AUTO 밸류체인만 쓴다. 실제 적재 데이터는 SEMICONDUCTOR라 서로 섞이지 않는다. */
@SpringBootTest
@Transactional
class EventServiceIntegrationTest {

    private static final UUID C1 = id(1);
    private static final UUID C2 = id(2);
    private static final UUID C3 = id(3);
    private static final UUID C4 = id(4);
    private static final UUID E1 = id(21);
    private static final UUID E2 = id(22);
    private static final UUID E3 = id(23);
    private static final UUID E4 = id(24);
    private static final UUID SOURCE_ID = id(30);

    @Autowired
    private EventService eventService;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    private static UUID id(int n) {
        return UUID.fromString(String.format("7e000000-0000-0000-0000-%012d", n));
    }

    @BeforeEach
    void setUp() {
        jdbcTemplate.update("INSERT INTO data_source (source_id, name, source_type) VALUES (?, 'nexus-test-source', 'NEWS_API')",
                SOURCE_ID);
        for (String[] t : new String[][] {{"SUPPLY", "공급", "DIRECTED"}, {"INVEST", "투자", "DIRECTED"},
                {"PARTNER", "협력", "UNDIRECTED"}, {"COMPETE", "경쟁", "UNDIRECTED"}}) {
            jdbcTemplate.update("INSERT INTO relationship_type (code, name, directionality) VALUES (?, ?, ?) "
                    + "ON CONFLICT (code) DO NOTHING", t[0], t[1], t[2]);
        }
        insertCompany(C1, "테스트기업1", "990001");
        insertCompany(C2, "테스트기업2", "990002");
        insertCompany(C3, "테스트기업3", "990003");
        insertCompany(C4, "테스트기업4", "990004");

        insertEvent(E1, id(11), "TEST.E1", "이벤트 1", "2026-09-01T01:00:00Z", LocalDate.of(2026, 9, 1));
        insertEvent(E2, id(12), "TEST.E2", "이벤트 2", "2026-09-01T02:00:00Z", null);
        insertEvent(E3, id(13), "TEST.E3", "이벤트 3", "2026-09-01T03:00:00Z", LocalDate.of(2026, 9, 1));

        insertImpact(E1, C1, "REPORTED", 0, "1.0", "OK", null, "[]");
        insertImpact(E1, C2, "REPORTED", 0, "0.8", "OK", null, "[]");
        insertImpact(E1, C3, "INFERRED", 1, "0.6", "OK", null,
                "[{\"src\":\"KOSDAQ:990001\",\"dst\":\"KOSDAQ:990003\",\"rel\":\"PARTNER\",\"source\":\"NEWS\",\"strength\":0.85}]");
        insertImpact(E1, C4, "INFERRED", 1, "0.2", "HOLD", "전이 강도 낮음",
                "[{\"src\":\"KOSDAQ:990002\",\"dst\":\"KOSDAQ:999999\",\"rel\":\"SUPPLY\",\"source\":\"COSMOS\",\"strength\":1.0}]");
        insertImpact(E2, C2, "REPORTED", 0, "1.0", "OK", null, "[]");
        insertImpact(E2, C1, "INFERRED", 1, "0.5", "OK", null,
                "[{\"src\":\"KOSDAQ:990002\",\"dst\":\"KOSDAQ:990001\",\"rel\":\"SUPPLY\",\"source\":\"COSMOS\",\"strength\":1.0}]");
        insertImpact(E3, C1, "REPORTED", 0, "1.0", "OK", null, "[]");
        // 취소된 기사: 목록에도 상세에도 나오면 안 된다
        insertEvent(E4, id(14), "TEST.E4", "취소된 이벤트", "2026-09-01T04:00:00Z", LocalDate.of(2026, 9, 1));
        jdbcTemplate.update("UPDATE news_event SET status = 'CANCELLED' WHERE event_id = ?", E4);
        insertImpact(E4, C3, "REPORTED", 0, "1.0", "OK", null, "[]");

        // 근거 조각은 반대 방향(기업3 → 기업1)으로 넣는다. PARTNER는 무방향이라 양쪽에서 찾아야 한다.
        jdbcTemplate.update("""
                INSERT INTO relation_evidence_snippet
                    (snippet_id, source_company_id, target_company_id, relationship_type_id, bigkinds_news_id, title,
                     provider, published_at, original_url, snippet, pattern, confidence)
                SELECT ?, ?, ?, relationship_type_id, 'TEST.S1', '근거 기사', '테스트일보', ?, 'https://example.com/s1',
                       '테스트기업3과 테스트기업1이 협력한다.', 'enum+kw', 0.8
                FROM relationship_type WHERE code = 'PARTNER'
                """, id(41), C3, C1, Timestamp.from(Instant.parse("2026-08-30T00:00:00Z")));
        jdbcTemplate.update("""
                INSERT INTO event_abnormal_return
                    (event_id, company_id, window_days, benchmark, stock_return, benchmark_return, abnormal_return)
                VALUES (?, ?, 1, 'KOSDAQ', 0.02, 0.005, 0.015), (?, ?, 3, 'KOSDAQ', 0.05, 0.01, 0.04)
                """, E1, C1, E1, C1);
    }

    @Test
    void mapOrdersReportedThenInferred() {
        EventMapResponse map = eventService.findEventMap(E1);

        assertThat(map.event().eventId()).isEqualTo(E1);
        assertThat(map.nodes()).extracting(EventNodeResponse::basis)
                .containsExactly("REPORTED", "REPORTED", "INFERRED", "INFERRED");
        assertThat(map.nodes()).extracting(EventNodeResponse::companyId).containsExactly(C1, C2, C3, C4);
    }

    @Test
    void inferredNodeHasAnchorAndNamedPath() {
        EventNodeResponse c3 = node(eventService.findEventMap(E1), C3);

        assertThat(c3.anchorCompanyId()).isEqualTo(C1);
        assertThat(c3.path()).hasSize(1);
        assertThat(c3.path().getFirst().sourceName()).isEqualTo("테스트기업1");
        assertThat(c3.path().getFirst().targetName()).isEqualTo("테스트기업3");
        assertThat(c3.path().getFirst().relationshipType()).isEqualTo("PARTNER");
        assertThat(c3.path().getFirst().edgeSource()).isEqualTo("NEWS");
        assertThat(node(eventService.findEventMap(E1), C1).anchorCompanyId()).isNull();
    }

    @Test
    void newsStepCarriesSnippetFoundInEitherDirection() {
        EventNodeResponse c3 = node(eventService.findEventMap(E1), C3);

        assertThat(c3.path().getFirst().evidence()).extracting(EvidenceSnippetResponse::snippet)
                .containsExactly("테스트기업3과 테스트기업1이 협력한다.");
    }

    @Test
    void unknownKeyInPathDoesNotFail() {
        EventNodeResponse c4 = node(eventService.findEventMap(E1), C4);

        assertThat(c4.status()).isEqualTo("HOLD");
        assertThat(c4.holdReason()).isEqualTo("전이 강도 낮음");
        assertThat(c4.anchorCompanyId()).isEqualTo(C2);
        assertThat(c4.path().getFirst().targetCompanyId()).isNull();
        assertThat(c4.path().getFirst().targetName()).isNull();
        assertThat(c4.path().getFirst().evidence()).isEmpty();   // COSMOS step: no snippets
    }

    @Test
    void abnormalReturnsAttached() {
        EventNodeResponse c1 = node(eventService.findEventMap(E1), C1);

        assertThat(c1.abnormalReturns()).extracting(AbnormalReturnResponse::windowDays).containsExactly(1, 3);
        assertThat(c1.abnormalReturns().getFirst().abnormalReturn()).isEqualByComparingTo("0.015");
        assertThat(node(eventService.findEventMap(E1), C3).abnormalReturns()).isEmpty();
    }

    @Test
    void eventWithoutTradingDateOrInferred() {
        assertThat(eventService.findEventMap(E2).event().tradingDate()).isNull();
        assertThat(eventService.findEventMap(E3).nodes()).extracting(EventNodeResponse::basis)
                .containsExactly("REPORTED");
    }

    @Test
    void cancelledEventIsHiddenFromListAndDetail() {
        assertThat(eventService.findEvents("AUTO", null, false, null, 20).items())
                .extracting(EventSummaryResponse::eventId).doesNotContain(E4);
        assertThatThrownBy(() -> eventService.findEventMap(E4))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode()).isEqualTo(EventErrorCode.EVENT_NOT_FOUND);
    }

    @Test
    void requiresValueChain() {
        // 밸류체인 없이 조회하면 news_event 전체를 훑는다(실데이터 1~4.7초). 인덱스를 타는 경로만 허용한다.
        assertThatThrownBy(() -> eventService.findEvents(null, C3, false, null, 20))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode()).isEqualTo(CommonErrorCode.VALIDATION_FAILED);
    }

    @Test
    void unknownEventThrowsNotFound() {
        assertThatThrownBy(() -> eventService.findEventMap(id(99)))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode()).isEqualTo(EventErrorCode.EVENT_NOT_FOUND);
    }

    private static EventNodeResponse node(EventMapResponse map, UUID companyId) {
        return map.nodes().stream().filter(n -> companyId.equals(n.companyId())).findFirst().orElseThrow();
    }

    @Test
    void findsEventsNewestFirstForValueChain() {
        CursorPageResponse<EventSummaryResponse> page = eventService.findEvents("AUTO", null, false, null, 20);

        assertThat(page.items()).extracting(EventSummaryResponse::eventId).containsExactly(E3, E2, E1);
        assertThat(page.hasNext()).isFalse();
    }

    @Test
    void pagesWithCursor() {
        CursorPageResponse<EventSummaryResponse> first = eventService.findEvents("AUTO", null, false, null, 1);
        CursorPageResponse<EventSummaryResponse> second =
                eventService.findEvents("AUTO", null, false, first.nextCursor(), 1);

        assertThat(first.items()).extracting(EventSummaryResponse::eventId).containsExactly(E3);
        assertThat(first.hasNext()).isTrue();
        assertThat(second.items()).extracting(EventSummaryResponse::eventId).containsExactly(E2);
    }

    @Test
    void filtersByCompanyAndInferred() {
        assertThat(eventService.findEvents("AUTO", C3, false, null, 20).items())
                .extracting(EventSummaryResponse::eventId).containsExactly(E1);
        assertThat(eventService.findEvents("AUTO", null, true, null, 20).items())
                .extracting(EventSummaryResponse::eventId).containsExactly(E2, E1);
    }

    @Test
    void summaryCounts() {
        EventSummaryResponse e1 = eventService.findEvents("AUTO", null, false, null, 20).items().get(2);

        assertThat(e1.reportedCompanies()).extracting(EventCompanyRefResponse::companyId).containsExactly(C1, C2);
        assertThat(e1.inferredCount()).isEqualTo(1);
        assertThat(e1.holdCount()).isEqualTo(1);
        assertThat(e1.valueChain()).isEqualTo("AUTO");
        assertThat(e1.tradingDate()).isEqualTo(LocalDate.of(2026, 9, 1));
        assertThat(e1.dateline()).isEqualTo(Instant.parse("2026-09-01T01:00:00Z"));
    }

    @Test
    void rejectsUnknownValueChainAndSize() {
        assertThatThrownBy(() -> eventService.findEvents("FOOD", null, false, null, 20))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode()).isEqualTo(CommonErrorCode.VALIDATION_FAILED);
        assertThatThrownBy(() -> eventService.findEvents("AUTO", null, false, null, 0))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode()).isEqualTo(CommonErrorCode.VALIDATION_FAILED);
    }

    private void insertCompany(UUID companyId, String name, String stockCode) {
        jdbcTemplate.update("INSERT INTO company (company_id, name, stock_code, market, status) VALUES (?, ?, ?, 'KOSDAQ', 'ACTIVE')",
                companyId, name, stockCode);
    }

    private void insertEvent(UUID eventId, UUID documentId, String newsId, String title, String dateline,
            LocalDate tradingDate) {
        Timestamp at = Timestamp.from(Instant.parse(dateline));
        jdbcTemplate.update("""
                INSERT INTO source_document (document_id, source_id, document_type, title, summary, original_url,
                                             published_at, status)
                VALUES (?, ?, 'NEWS', ?, '요약', 'https://example.com/' || ?, ?, 'ANALYZED')
                """, documentId, SOURCE_ID, title, newsId, at);
        jdbcTemplate.update("""
                INSERT INTO news_article (document_id, publisher, canonical_url, canonical_url_hash)
                VALUES (?, '테스트일보', 'bigkinds://news/' || ?, md5(?) || md5(? || 'x'))
                """, documentId, newsId, newsId, newsId);
        jdbcTemplate.update("""
                INSERT INTO news_event (event_id, document_id, bigkinds_news_id, value_chain, categories, dateline,
                                        event_trading_date, pipeline_version)
                VALUES (?, ?, ?, 'AUTO', ARRAY['경제>자동차'], ?, ?, 'test')
                """, eventId, documentId, newsId, at, tradingDate);
    }

    private void insertImpact(UUID eventId, UUID companyId, String basis, int hop, String score, String status,
            String holdReason, String pathJson) {
        jdbcTemplate.update("""
                INSERT INTO event_company_impact (event_id, company_id, basis, hop, path, propagation_score, status,
                                                  hold_reason, model_version)
                VALUES (?, ?, ?, ?, CAST(? AS JSONB), CAST(? AS NUMERIC), ?, ?, 'test')
                """, eventId, companyId, basis, hop, pathJson, score, status, holdReason);
    }
}
