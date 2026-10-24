package com.cosmos.api.event.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.cosmos.api.event.dto.AbnormalReturnResponse;
import com.cosmos.api.event.dto.BacktestResponse;
import com.cosmos.api.event.dto.BacktestRowResponse;
import com.cosmos.api.event.dto.ChartPointResponse;
import com.cosmos.api.event.dto.CompanyHistoryItemResponse;
import com.cosmos.api.event.dto.CompanySignalResponse;
import com.cosmos.api.event.dto.EventChartResponse;
import com.cosmos.api.event.dto.EventCompanyRefResponse;
import com.cosmos.api.event.dto.EventMapResponse;
import com.cosmos.api.event.dto.EventNodeResponse;
import com.cosmos.api.event.dto.EventSummaryResponse;
import com.cosmos.api.event.dto.EvidenceSnippetResponse;
import com.cosmos.api.event.dto.SignalCountsResponse.HorizonCount;
import com.cosmos.api.event.error.EventErrorCode;
import com.cosmos.api.event.repository.EventQueryRepository;
import com.cosmos.api.global.cursor.TimeIdCursor;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import com.cosmos.api.global.response.CursorPageResponse;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
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
    private static final UUID C5 = id(5);
    private static final UUID E1 = id(21);
    private static final UUID E2 = id(22);
    private static final UUID E3 = id(23);
    private static final UUID E4 = id(24);
    private static final UUID E5 = id(25);
    private static final UUID SOURCE_ID = id(30);

    @Autowired
    private EventService eventService;

    @Autowired
    private EventQueryRepository eventRepository;

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
                VALUES (?, ?, 1, 'KOSDAQ', 0.02, 0.005, 0.015), (?, ?, 3, 'KOSDAQ', 0.05, 0.01, 0.04),
                       (?, ?, 5, 'KOSDAQ', 0.06, 0.01, 0.05), (?, ?, 20, 'KOSDAQ', 0.10, 0.03, 0.07)
                """, E1, C1, E1, C1, E1, C1, E1, C1);

        // 뉴스 유형: E1 실적 부진, E2 기타, E3 미분류(null)
        jdbcTemplate.update("UPDATE news_event SET event_type = 'EARNINGS_MISS', event_type_confidence = 0.9 "
                + "WHERE event_id = ?", E1);
        jdbcTemplate.update("UPDATE news_event SET event_type = 'OTHER' WHERE event_id = ?", E2);

        // 방향 딱지: E1의 C1·C3·C4만(C2는 없음). SHORT 업1·다운1·보류1, LONG 다운1·보류2
        insertSignal(E1, C1, "SHORT", "REPORTED", "UP", null, "COMPANY", 12, "0.75", "0.021", "0.25");
        insertSignal(E1, C1, "LONG", "REPORTED", "HOLD", "사례 부족", "POSITION", 4, "0.5", "0.01", "0.5");
        insertSignal(E1, C3, "SHORT", "PARTNER", "DOWN", null, "POSITION", 15, "0.7", "-0.015", "0.7");
        insertSignal(E1, C3, "LONG", "PARTNER", "DOWN", null, "POSITION", 15, "0.8", "-0.04", "0.8");
        insertSignal(E1, C4, "SHORT", "SUPPLIER", "HOLD", "연결이 약함", null, 0, null, null, null);
        insertSignal(E1, C4, "LONG", "SUPPLIER", "HOLD", "연결이 약함", null, 0, null, null, null);
        insertSignal(E3, C1, "SHORT", "REPORTED", "HOLD", "비슷한 유형 없음", null, 0, null, null, null);

        // 기업 지표: C1만 있다
        jdbcTemplate.update("""
                INSERT INTO company_fundamental (company_id, market_cap, market_cap_rank, size_class, net_income_ttm,
                    equity, dividend_per_share, close_price, per, pbr, dividend_yield, fiscal_basis, price_date, source)
                VALUES (?, 1500000000000, 3, 'MID', 100000000000, 750000000000, 500, 30000, 15.0, 2.0, 0.016667,
                        '2026 2분기 누적 TTM', DATE '2026-10-16', 'test')
                """, C1);
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

        assertThat(c1.abnormalReturns()).extracting(AbnormalReturnResponse::windowDays).containsExactly(1, 3, 5, 20);
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
        assertThat(eventService.findEvents("AUTO", null, null, false, null, 20).items())
                .extracting(EventSummaryResponse::eventId).doesNotContain(E4);
        assertThatThrownBy(() -> eventService.findEventMap(E4))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode()).isEqualTo(EventErrorCode.EVENT_NOT_FOUND);
    }

    @Test
    void requiresValueChain() {
        // 밸류체인 없이 조회하면 news_event 전체를 훑는다(실데이터 1~4.7초). 인덱스를 타는 경로만 허용한다.
        assertThatThrownBy(() -> eventService.findEvents(null, C3, null, false, null, 20))
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
        CursorPageResponse<EventSummaryResponse> page = eventService.findEvents("AUTO", null, null, false, null, 20);

        assertThat(page.items()).extracting(EventSummaryResponse::eventId).containsExactly(E3, E2, E1);
        assertThat(page.hasNext()).isFalse();
    }

    @Test
    void pagesWithCursor() {
        CursorPageResponse<EventSummaryResponse> first = eventService.findEvents("AUTO", null, null, false, null, 1);
        CursorPageResponse<EventSummaryResponse> second =
                eventService.findEvents("AUTO", null, null, false, first.nextCursor(), 1);

        assertThat(first.items()).extracting(EventSummaryResponse::eventId).containsExactly(E3);
        assertThat(first.hasNext()).isTrue();
        assertThat(second.items()).extracting(EventSummaryResponse::eventId).containsExactly(E2);
    }

    @Test
    void filtersByCompanyAndInferred() {
        assertThat(eventService.findEvents("AUTO", C3, null, false, null, 20).items())
                .extracting(EventSummaryResponse::eventId).containsExactly(E1);
        assertThat(eventService.findEvents("AUTO", null, null, true, null, 20).items())
                .extracting(EventSummaryResponse::eventId).containsExactly(E2, E1);
    }

    @Test
    void summaryCounts() {
        EventSummaryResponse e1 = eventService.findEvents("AUTO", null, null, false, null, 20).items().get(2);

        assertThat(e1.reportedCompanies()).extracting(EventCompanyRefResponse::companyId).containsExactly(C1, C2);
        assertThat(e1.inferredCount()).isEqualTo(1);
        assertThat(e1.holdCount()).isEqualTo(1);
        assertThat(e1.valueChain()).isEqualTo("AUTO");
        assertThat(e1.tradingDate()).isEqualTo(LocalDate.of(2026, 9, 1));
        assertThat(e1.dateline()).isEqualTo(Instant.parse("2026-09-01T01:00:00Z"));
    }

    @Test
    void rejectsUnknownValueChainAndSize() {
        assertThatThrownBy(() -> eventService.findEvents("FOOD", null, null, false, null, 20))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode()).isEqualTo(CommonErrorCode.VALIDATION_FAILED);
        assertThatThrownBy(() -> eventService.findEvents("AUTO", null, null, false, null, 0))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode()).isEqualTo(CommonErrorCode.VALIDATION_FAILED);
    }

    @Test
    void impactModelFieldsPassThrough() {
        jdbcTemplate.update("UPDATE event_company_impact SET impact_direction = 'POSITIVE', impact_magnitude = 0.31, "
                + "confidence = 0.62 WHERE event_id = ? AND company_id = ?", E1, C1);

        EventMapResponse map = eventService.findEventMap(E1);

        EventNodeResponse c1 = node(map, C1);
        assertThat(c1.impactDirection()).isEqualTo("POSITIVE");
        assertThat(c1.impactMagnitude()).isEqualByComparingTo("0.31");
        assertThat(c1.confidence()).isEqualByComparingTo("0.62");
        EventNodeResponse c2 = node(map, C2);
        assertThat(c2.impactDirection()).isNull();
        assertThat(c2.impactMagnitude()).isNull();
        assertThat(c2.confidence()).isNull();
    }

    // ---- 투자자용(V12): 뉴스 유형 · 방향 딱지 · 기업 지표 · 차트 · 적중률 · 기업 과거 뉴스 ----

    @Test
    void summaryCarriesNewsTypeAndSignalCounts() {
        List<EventSummaryResponse> items = eventService.findEvents("AUTO", null, null, false, null, 20).items();
        EventSummaryResponse e3 = items.get(0);
        EventSummaryResponse e2 = items.get(1);
        EventSummaryResponse e1 = items.get(2);

        assertThat(e1.newsType()).isEqualTo("EARNINGS_MISS");
        assertThat(e1.newsTypeLabel()).isEqualTo("실적 부진");
        assertThat(e1.signalCounts().shortTerm()).isEqualTo(new HorizonCount(1, 1, 1));
        assertThat(e1.signalCounts().longTerm()).isEqualTo(new HorizonCount(0, 1, 2));
        assertThat(e2.newsTypeLabel()).isEqualTo("기타");
        assertThat(e2.signalCounts().shortTerm()).isEqualTo(new HorizonCount(0, 0, 0));
        assertThat(e2.signalCounts().longTerm()).isEqualTo(new HorizonCount(0, 0, 0));
        assertThat(e3.newsType()).isNull();
        assertThat(e3.newsTypeLabel()).isNull();
        assertThat(e3.signalCounts().shortTerm()).isEqualTo(new HorizonCount(0, 0, 1));
    }

    @Test
    void filtersByNewsType() {
        assertThat(eventService.findEvents("AUTO", null, "EARNINGS_MISS", false, null, 20).items())
                .extracting(EventSummaryResponse::eventId).containsExactly(E1);
        assertThat(eventService.findEvents("AUTO", null, "OTHER", false, null, 20).items())
                .extracting(EventSummaryResponse::eventId).containsExactly(E2);
        assertThat(eventService.findEvents("AUTO", null, "TARIFF", false, null, 20).items()).isEmpty();
        assertErrorCode(() -> eventService.findEvents("AUTO", null, "FOO", false, null, 20),
                CommonErrorCode.VALIDATION_FAILED);
        assertErrorCode(() -> eventService.findEvents("AUTO", null, "earnings_miss", false, null, 20),
                CommonErrorCode.VALIDATION_FAILED);
    }

    @Test
    void mapSummaryCountsSignalsOfEvent() {
        EventSummaryResponse event = eventService.findEventMap(E1).event();

        assertThat(event.newsType()).isEqualTo("EARNINGS_MISS");
        assertThat(event.newsTypeLabel()).isEqualTo("실적 부진");
        assertThat(event.signalCounts().shortTerm()).isEqualTo(new HorizonCount(1, 1, 1));
        assertThat(event.signalCounts().longTerm()).isEqualTo(new HorizonCount(0, 1, 2));
    }

    @Test
    void nodeCarriesPositionAndSignalsShortThenLong() {
        EventMapResponse map = eventService.findEventMap(E1);
        EventNodeResponse c1 = node(map, C1);

        assertThat(c1.position()).isEqualTo("REPORTED");
        assertThat(c1.positionLabel()).isEqualTo("기사에 나온 기업");
        assertThat(c1.signals()).extracting(CompanySignalResponse::horizon).containsExactly("SHORT", "LONG");
        assertThat(c1.signals()).extracting(CompanySignalResponse::windowDays).containsExactly(3, 20);
        CompanySignalResponse shortSignal = c1.signals().get(0);
        assertThat(shortSignal.label()).isEqualTo("UP");
        assertThat(shortSignal.holdReason()).isNull();
        assertThat(shortSignal.caseScope()).isEqualTo("COMPANY");
        assertThat(shortSignal.caseCount()).isEqualTo(12);
        assertThat(shortSignal.sameShare()).isEqualByComparingTo("0.75");
        assertThat(shortSignal.meanAr()).isEqualByComparingTo("0.021");
        assertThat(shortSignal.downShare()).isEqualByComparingTo("0.25");
        assertThat(c1.signals().get(1).label()).isEqualTo("HOLD");
        assertThat(c1.signals().get(1).holdReason()).isEqualTo("사례 부족");

        EventNodeResponse c3 = node(map, C3);
        assertThat(c3.position()).isEqualTo("PARTNER");
        assertThat(c3.positionLabel()).isEqualTo("협력사");
        EventNodeResponse c4 = node(map, C4);
        assertThat(c4.positionLabel()).isEqualTo("공급사");
        assertThat(c4.signals().getFirst().caseScope()).isNull();
        assertThat(c4.signals().getFirst().sameShare()).isNull();

        EventNodeResponse c2 = node(map, C2);
        assertThat(c2.position()).isNull();
        assertThat(c2.positionLabel()).isNull();
        assertThat(c2.signals()).isEmpty();
    }

    @Test
    void nodeCarriesFundamentalOrNull() {
        EventMapResponse map = eventService.findEventMap(E1);

        var fundamental = node(map, C1).fundamental();
        assertThat(fundamental).isNotNull();
        assertThat(fundamental.marketCap()).isEqualByComparingTo("1500000000000");
        assertThat(fundamental.marketCapRank()).isEqualTo(3);
        assertThat(fundamental.sizeClass()).isEqualTo("MID");
        assertThat(fundamental.per()).isEqualByComparingTo("15.0");
        assertThat(fundamental.pbr()).isEqualByComparingTo("2.0");
        assertThat(fundamental.dividendYield()).isEqualByComparingTo("0.016667");
        assertThat(fundamental.priceDate()).isEqualTo(LocalDate.of(2026, 10, 16));
        assertThat(fundamental.fiscalBasis()).isEqualTo("2026 2분기 누적 TTM");
        assertThat(node(map, C2).fundamental()).isNull();
    }

    /** 달력: 2031-03-12(이벤트 거래일) 앞 6거래일, 당일 포함 3거래일. 기업 종가는 03-06·03-13이 빠져 있다. */
    private void seedChart() {
        insertEvent(E5, id(15), "TEST.E5", "차트 이벤트", "2031-03-12T01:00:00Z", LocalDate.of(2031, 3, 12));
        insertImpact(E5, C1, "REPORTED", 0, "1.0", "OK", null, "[]");
        String[][] index = {{"2031-03-04", "900"}, {"2031-03-05", "950"}, {"2031-03-06", "960"},
                {"2031-03-07", "970"}, {"2031-03-10", "990"}, {"2031-03-11", "1000"}, {"2031-03-12", "1010"},
                {"2031-03-13", "1020"}, {"2031-03-14", "990"}};
        for (String[] row : index) {
            insertIndex("KOSDAQ", row[0], row[1]);
        }
        String[][] closes = {{"2031-03-04", "8000"}, {"2031-03-05", "9000"}, {"2031-03-07", "9500"},
                {"2031-03-10", "9800"}, {"2031-03-11", "10000"}, {"2031-03-12", "10500"}, {"2031-03-14", "9900"}};
        for (String[] row : closes) {
            insertDailyClose(C1, row[0], row[1]);
        }
    }

    @Test
    void chartHasOffsetsAndCumulativeReturnsFromDayBefore() {
        seedChart();

        EventChartResponse chart = eventService.findChart(E5, C1);

        assertThat(chart.tradingDate()).isEqualTo(LocalDate.of(2031, 3, 12));
        assertThat(chart.benchmark()).isEqualTo("KOSDAQ");
        // 앞 5거래일만(03-04는 빠짐), 달력이 03-14에서 끝나 +2까지
        assertThat(chart.points()).extracting(ChartPointResponse::offset).containsExactly(-5, -4, -3, -2, -1, 0, 1, 2);
        assertThat(chart.points().getFirst().date()).isEqualTo(LocalDate.of(2031, 3, 5));

        ChartPointResponse first = chart.points().get(0);
        assertThat(first.close()).isEqualByComparingTo("9000");
        assertThat(first.stockReturn()).isEqualByComparingTo("-0.1");
        assertThat(first.indexReturn()).isEqualByComparingTo("-0.05");
        assertThat(first.abnormalReturn()).isEqualByComparingTo("-0.05");

        ChartPointResponse missing = chart.points().get(1);   // 03-06: 기업 종가 없음
        assertThat(missing.close()).isNull();
        assertThat(missing.stockReturn()).isNull();
        assertThat(missing.abnormalReturn()).isNull();
        assertThat(missing.indexReturn()).isEqualByComparingTo("-0.04");

        ChartPointResponse base = chart.points().get(4);
        assertThat(base.date()).isEqualTo(LocalDate.of(2031, 3, 11));
        assertThat(base.stockReturn()).isEqualByComparingTo("0");
        assertThat(base.indexReturn()).isEqualByComparingTo("0");

        ChartPointResponse day0 = chart.points().get(5);
        assertThat(day0.date()).isEqualTo(LocalDate.of(2031, 3, 12));
        assertThat(day0.close()).isEqualByComparingTo("10500");
        assertThat(day0.indexClose()).isEqualByComparingTo("1010");
        assertThat(day0.stockReturn()).isEqualByComparingTo("0.05");
        assertThat(day0.indexReturn()).isEqualByComparingTo("0.01");
        assertThat(day0.abnormalReturn()).isEqualByComparingTo("0.04");

        assertThat(chart.points().get(6).close()).isNull();
        ChartPointResponse last = chart.points().get(7);
        assertThat(last.stockReturn()).isEqualByComparingTo("-0.01");
        assertThat(last.indexReturn()).isEqualByComparingTo("-0.01");
        assertThat(last.abnormalReturn()).isEqualByComparingTo("0");
    }

    @Test
    void chartReturnsAreNullWithoutBaseClose() {
        seedChart();
        jdbcTemplate.update("DELETE FROM stock_price_history WHERE company_id = ? "
                + "AND trading_at = TIMESTAMPTZ '2031-03-11 00:00:00+00'", C1);

        EventChartResponse chart = eventService.findChart(E5, C1);

        assertThat(chart.points()).allSatisfy(p -> {
            assertThat(p.stockReturn()).isNull();
            assertThat(p.abnormalReturn()).isNull();
        });
        assertThat(chart.points().get(5).close()).isEqualByComparingTo("10500");
        assertThat(chart.points().get(5).indexReturn()).isEqualByComparingTo("0.01");
    }

    @Test
    void chartNotFoundCases() {
        insertCompany(C5, "해외기업", "TSTX", "NASDAQ");
        insertImpact(E3, C5, "INFERRED", 1, "0.4", "OK", null, "[]");

        assertErrorCode(() -> eventService.findChart(id(99), C1), EventErrorCode.EVENT_NOT_FOUND);
        assertErrorCode(() -> eventService.findChart(E4, C3), EventErrorCode.EVENT_NOT_FOUND);   // 취소된 기사
        assertErrorCode(() -> eventService.findChart(E3, C2), EventErrorCode.EVENT_COMPANY_NOT_FOUND);
        assertErrorCode(() -> eventService.findChart(E2, C2), EventErrorCode.EVENT_CHART_UNAVAILABLE);   // 거래일 없음
        assertErrorCode(() -> eventService.findChart(E3, C5), EventErrorCode.EVENT_CHART_UNAVAILABLE);   // 해외 상장
    }

    @Test
    void backtestOrdersAllTypesFirstThenByLabeledCount() {
        insertBacktest("LONG", "TARIFF", 10, 8, 4, 6, 2, "0.3");
        insertBacktest("SHORT", "OTHER", 50, 0, 0, 0, 0, "0.4");
        insertBacktest("SHORT", "EARNINGS_MISS", 20, 5, 3, 1, 4, "0.6");
        insertBacktest("LONG", null, 90, 30, 12, 20, 10, null);
        insertBacktest("SHORT", "CAPEX", 30, 12, 6, 10, 2, "0.5");
        insertBacktest("SHORT", null, 100, 40, 24, 30, 10, "0.45");

        BacktestResponse backtest = eventService.findBacktest("AUTO");

        assertThat(backtest.valueChain()).isEqualTo("AUTO");
        assertThat(backtest.trainEnd()).isEqualTo(LocalDate.of(2026, 6, 30));
        assertThat(backtest.testStart()).isEqualTo(LocalDate.of(2026, 7, 1));
        assertThat(backtest.testEnd()).isEqualTo(LocalDate.of(2026, 10, 14));
        assertThat(backtest.rows()).extracting(BacktestRowResponse::horizon)
                .containsExactly("SHORT", "SHORT", "SHORT", "SHORT", "LONG", "LONG");
        assertThat(backtest.rows()).extracting(BacktestRowResponse::newsType)
                .containsExactly(null, "CAPEX", "EARNINGS_MISS", "OTHER", null, "TARIFF");

        BacktestRowResponse all = backtest.rows().get(0);
        assertThat(all.newsTypeLabel()).isNull();
        assertThat(all.hitRate()).isEqualByComparingTo("0.6");
        assertThat(all.baselineRate()).isEqualByComparingTo("0.55");
        assertThat(all.caseCount()).isEqualTo(100);
        assertThat(all.upLabels()).isEqualTo(30);
        assertThat(all.downLabels()).isEqualTo(10);
        BacktestRowResponse earnings = backtest.rows().get(2);
        assertThat(earnings.newsTypeLabel()).isEqualTo("실적 부진");
        assertThat(earnings.hitRate()).isEqualByComparingTo("0.6");
        assertThat(earnings.baselineRate()).isEqualByComparingTo("0.6");
        BacktestRowResponse other = backtest.rows().get(3);
        assertThat(other.hitRate()).isNull();   // 딱지 0건
        assertThat(other.baselineRate()).isEqualByComparingTo("0.6");
        BacktestRowResponse longAll = backtest.rows().get(4);
        assertThat(longAll.hitRate()).isEqualByComparingTo("0.4");
        assertThat(longAll.baseDownShare()).isNull();
        assertThat(longAll.baselineRate()).isNull();
    }

    @Test
    void backtestIsEmptyBeforeComputedAndRequiresValueChain() {
        BacktestResponse empty = eventService.findBacktest("AUTO");

        assertThat(empty.rows()).isEmpty();
        assertThat(empty.trainEnd()).isNull();
        assertThat(empty.testStart()).isNull();
        assertThat(empty.testEnd()).isNull();
        assertErrorCode(() -> eventService.findBacktest(null), CommonErrorCode.VALIDATION_FAILED);
        assertErrorCode(() -> eventService.findBacktest("FOOD"), CommonErrorCode.VALIDATION_FAILED);
    }

    @Test
    void companyHistoryPagesNewestFirstWithCompanySignalsAndReturns() {
        CursorPageResponse<CompanyHistoryItemResponse> first = eventService.findCompanyHistory(C1, null, 2);
        CursorPageResponse<CompanyHistoryItemResponse> second =
                eventService.findCompanyHistory(C1, first.nextCursor(), 2);

        assertThat(first.items()).extracting(i -> i.event().eventId()).containsExactly(E3, E2);
        assertThat(first.hasNext()).isTrue();
        assertThat(second.items()).extracting(i -> i.event().eventId()).containsExactly(E1);
        assertThat(second.hasNext()).isFalse();
        assertThat(second.nextCursor()).isNull();

        CompanyHistoryItemResponse e2 = first.items().get(1);   // C1은 E2에서 추정 OK, 딱지·수익률 없음
        assertThat(e2.basis()).isEqualTo("INFERRED");
        assertThat(e2.position()).isNull();
        assertThat(e2.positionLabel()).isNull();
        assertThat(e2.signals()).isEmpty();
        assertThat(e2.abnormalReturns()).isEmpty();
        assertThat(e2.event().reportedCompanies()).extracting(EventCompanyRefResponse::companyId).containsExactly(C2);

        CompanyHistoryItemResponse e1 = second.items().getFirst();
        assertThat(e1.basis()).isEqualTo("REPORTED");
        assertThat(e1.position()).isEqualTo("REPORTED");
        assertThat(e1.positionLabel()).isEqualTo("기사에 나온 기업");
        assertThat(e1.signals()).extracting(CompanySignalResponse::label).containsExactly("UP", "HOLD");
        assertThat(e1.abnormalReturns()).extracting(AbnormalReturnResponse::windowDays).containsExactly(1, 3, 5, 20);
        assertThat(e1.abnormalReturns().get(3).abnormalReturn()).isEqualByComparingTo("0.07");
        assertThat(e1.event().newsTypeLabel()).isEqualTo("실적 부진");
        assertThat(e1.event().signalCounts().longTerm()).isEqualTo(new HorizonCount(0, 1, 2));
    }

    @Test
    void companyEventsBothQueryPlansReturnSameRows() {
        // 서비스는 OK 영향 수로 계획을 고른다(시드 기업은 적어서 늘 "기업 영향부터"). 두 SQL이 같은 결과인지 직접 본다.
        for (UUID company : List.of(C1, C3, C4)) {
            List<UUID> byDateline = eventRepository.findCompanyEvents(company, null, 10, true).stream()
                    .map(r -> r.event().eventId()).toList();
            List<UUID> byCompany = eventRepository.findCompanyEvents(company, null, 10, false).stream()
                    .map(r -> r.event().eventId()).toList();
            assertThat(byDateline).isEqualTo(byCompany);
        }
        var first = eventRepository.findCompanyEvents(C1, null, 1, true).getFirst().event();
        var cursor = new TimeIdCursor(first.dateline(), first.eventId());
        assertThat(eventRepository.findCompanyEvents(C1, cursor, 10, true))
                .extracting(r -> r.event().eventId()).containsExactly(E2, E1);
        assertThat(eventRepository.findCompanyEvents(C1, cursor, 10, false))
                .extracting(r -> r.event().eventId()).containsExactly(E2, E1);
    }

    @Test
    void companyHistoryOnlyOkImpactsOfActiveEvents() {
        // C4는 E1에서 HOLD뿐, C3은 E1(추정 OK)과 취소된 E4(언급)
        assertThat(eventService.findCompanyHistory(C4, null, 20).items()).isEmpty();
        assertThat(eventService.findCompanyHistory(C3, null, 20).items())
                .extracting(i -> i.event().eventId()).containsExactly(E1);
        assertThat(eventService.findCompanyHistory(id(98), null, 20).items()).isEmpty();
        assertErrorCode(() -> eventService.findCompanyHistory(null, null, 20), CommonErrorCode.VALIDATION_FAILED);
        assertErrorCode(() -> eventService.findCompanyHistory(C1, null, 0), CommonErrorCode.VALIDATION_FAILED);
    }

    private void insertCompany(UUID companyId, String name, String stockCode) {
        insertCompany(companyId, name, stockCode, "KOSDAQ");
    }

    private void insertCompany(UUID companyId, String name, String stockCode, String market) {
        jdbcTemplate.update("INSERT INTO company (company_id, name, stock_code, market, status) VALUES (?, ?, ?, ?, 'ACTIVE')",
                companyId, name, stockCode, market);
    }

    private void insertSignal(UUID eventId, UUID companyId, String horizon, String position, String label,
            String holdReason, String scope, int caseCount, String sameShare, String meanAr, String downShare) {
        jdbcTemplate.update("""
                INSERT INTO event_company_signal (event_id, company_id, horizon, position, label, hold_reason, case_scope,
                                                  case_count, same_share, mean_ar, down_share)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS NUMERIC), CAST(? AS NUMERIC), CAST(? AS NUMERIC))
                """, eventId, companyId, horizon, position, label, holdReason, scope, caseCount, sameShare, meanAr,
                downShare);
    }

    /** 지수 종가. 실데이터와 겹치지 않게 먼 미래(2031년) 날짜만 쓴다. */
    private void insertIndex(String indexCode, String date, String close) {
        jdbcTemplate.update("""
                INSERT INTO market_index_price (index_code, trading_date, close_price) VALUES (?, CAST(? AS DATE), CAST(? AS NUMERIC))
                ON CONFLICT (index_code, trading_date) DO UPDATE SET close_price = EXCLUDED.close_price
                """, indexCode, date, close);
    }

    private void insertDailyClose(UUID companyId, String date, String close) {
        jdbcTemplate.update("""
                INSERT INTO stock_price_history (company_id, trading_at, interval_type, open_price, high_price, low_price,
                                                 close_price, trading_volume)
                VALUES (?, CAST(? AS DATE)::timestamp AT TIME ZONE 'UTC', '1D', CAST(? AS NUMERIC), CAST(? AS NUMERIC),
                        CAST(? AS NUMERIC), CAST(? AS NUMERIC), 1000)
                """, companyId, date, close, close, close, close);
    }

    private void insertBacktest(String horizon, String newsType, int caseCount, int labeled, int hits, int up, int down,
            String baseDownShare) {
        jdbcTemplate.update("""
                INSERT INTO signal_backtest (value_chain, horizon, news_type, case_count, labeled_count, hit_count,
                                             up_labels, down_labels, base_down_share, train_end, test_start, test_end)
                VALUES ('AUTO', ?, ?, ?, ?, ?, ?, ?, CAST(? AS NUMERIC), DATE '2026-06-30', DATE '2026-07-01',
                        DATE '2026-10-14')
                """, horizon, newsType, caseCount, labeled, hits, up, down, baseDownShare);
    }

    private static void assertErrorCode(Runnable action, Object errorCode) {
        assertThatThrownBy(action::run)
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode()).isEqualTo(errorCode);
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
