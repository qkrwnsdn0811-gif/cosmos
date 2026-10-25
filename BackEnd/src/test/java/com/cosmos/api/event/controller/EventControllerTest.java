package com.cosmos.api.event.controller;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.company.dto.CompanyFundamentalResponse;
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
import com.cosmos.api.event.dto.ExpectedMoveResponse;
import com.cosmos.api.event.dto.MoveBacktestRowResponse;
import com.cosmos.api.event.dto.SignalCountsResponse;
import com.cosmos.api.event.dto.SignalCountsResponse.HorizonCount;
import com.cosmos.api.event.service.EventService;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import com.cosmos.api.global.error.GlobalExceptionHandler;
import com.cosmos.api.global.response.CursorPageResponse;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class EventControllerTest {

    private static final UUID EVENT_ID = UUID.fromString("7e100000-0000-0000-0000-000000000001");
    private static final UUID NEWS_ID = UUID.fromString("7e100000-0000-0000-0000-000000000002");
    private static final UUID COMPANY_ID = UUID.fromString("7e100000-0000-0000-0000-000000000003");

    private EventService eventService;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        eventService = mock(EventService.class);
        mockMvc = MockMvcBuilders.standaloneSetup(new EventController(eventService))
                .setControllerAdvice(new GlobalExceptionHandler())
                .build();
    }

    private static EventSummaryResponse summary() {
        return new EventSummaryResponse(EVENT_ID, NEWS_ID, "제목", "요약", "테스트일보", "https://example.com/1",
                Instant.parse("2026-10-13T01:00:00Z"), LocalDate.of(2026, 10, 13), "SEMICONDUCTOR",
                List.of("경제>반도체"), List.of(new EventCompanyRefResponse(COMPANY_ID, "기업", "990001", "KOSDAQ")), 3, 1,
                "EXPORT_CONTROL", "수출 규제·제재",
                new SignalCountsResponse(new HorizonCount(1, 2, 3), new HorizonCount(4, 5, 6)));
    }

    private static CompanySignalResponse signal() {
        return new CompanySignalResponse("SHORT", 3, "DOWN", null, "POSITION", 14, new BigDecimal("0.714286"),
                new BigDecimal("-0.0213"), new BigDecimal("0.714286"));
    }

    @Test
    void listPassesFiltersAndReturnsCamelCase() throws Exception {
        when(eventService.findEvents("SEMICONDUCTOR", null, null, true, null, 5))
                .thenReturn(CursorPageResponse.of(List.of(summary()), "next", true));

        mockMvc.perform(get("/api/events").param("valueChain", "SEMICONDUCTOR").param("hasInferred", "true")
                        .param("size", "5"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].eventId").value(EVENT_ID.toString()))
                .andExpect(jsonPath("$.items[0].dateline").value("2026-10-13T01:00:00Z"))
                .andExpect(jsonPath("$.items[0].reportedCompanies[0].stockCode").value("990001"))
                .andExpect(jsonPath("$.items[0].inferredCount").value(3))
                .andExpect(jsonPath("$.nextCursor").value("next"))
                .andExpect(jsonPath("$.hasNext").value(true));
        verify(eventService).findEvents("SEMICONDUCTOR", null, null, true, null, 5);
    }

    @Test
    void listReturnsNewsTypeAndSignalCountsKeyedByHorizon() throws Exception {
        when(eventService.findEvents("SEMICONDUCTOR", null, "EXPORT_CONTROL", false, null, 20))
                .thenReturn(CursorPageResponse.of(List.of(summary()), null, false));

        mockMvc.perform(get("/api/events").param("valueChain", "SEMICONDUCTOR").param("newsType", "EXPORT_CONTROL"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].newsType").value("EXPORT_CONTROL"))
                .andExpect(jsonPath("$.items[0].newsTypeLabel").value("수출 규제·제재"))
                .andExpect(jsonPath("$.items[0].signalCounts.SHORT.up").value(1))
                .andExpect(jsonPath("$.items[0].signalCounts.SHORT.down").value(2))
                .andExpect(jsonPath("$.items[0].signalCounts.SHORT.hold").value(3))
                .andExpect(jsonPath("$.items[0].signalCounts.LONG.up").value(4))
                .andExpect(jsonPath("$.items[0].signalCounts.LONG.hold").value(6))
                .andExpect(jsonPath("$.items[0].signalCounts.shortTerm").doesNotExist());
        verify(eventService).findEvents("SEMICONDUCTOR", null, "EXPORT_CONTROL", false, null, 20);
    }

    @Test
    void invalidCursorIs400() throws Exception {
        when(eventService.findEvents(null, null, null, false, "broken", 20))
                .thenThrow(new BusinessException(CommonErrorCode.INVALID_CURSOR));

        mockMvc.perform(get("/api/events").param("cursor", "broken"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_CURSOR"));
    }

    @Test
    void mapReturnsNodes() throws Exception {
        CompanyFundamentalResponse fundamental = new CompanyFundamentalResponse(new BigDecimal("1500000000000"), 3,
                "MID", new BigDecimal("15.0"), new BigDecimal("2.0"), new BigDecimal("0.016667"), null, null, null,
                new BigDecimal("30000"), LocalDate.of(2026, 10, 16), "TTM");
        EventNodeResponse node = new EventNodeResponse(COMPANY_ID, "기업", "990001", "KOSDAQ", "반도체 장비", "REPORTED", 0,
                new BigDecimal("1.0"), "OK", null, null, null, null, null, List.of(),
                List.of(new AbnormalReturnResponse(20, "KOSDAQ", new BigDecimal("0.1"), new BigDecimal("0.03"),
                        new BigDecimal("0.07"))),
                "REPORTED", "기사에 나온 기업", List.of(signal()),
                List.of(new ExpectedMoveResponse("SHORT", 3, new BigDecimal("0.031"), 40, "LARGE")), fundamental);
        EventNodeResponse bare = new EventNodeResponse(COMPANY_ID, "기업2", "990002", "KOSDAQ", null, "INFERRED", 1,
                new BigDecimal("0.4"), "OK", null, null, null, null, null, List.of(), List.of(), null, null, List.of(),
                List.of(), null);
        when(eventService.findEventMap(EVENT_ID)).thenReturn(new EventMapResponse(summary(), List.of(node, bare)));

        mockMvc.perform(get("/api/events/{eventId}", EVENT_ID))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.event.tradingDate").value("2026-10-13"))
                .andExpect(jsonPath("$.event.signalCounts.LONG.down").value(5))
                .andExpect(jsonPath("$.nodes[0].basis").value("REPORTED"))
                .andExpect(jsonPath("$.nodes[0].industry").value("반도체 장비"))
                .andExpect(jsonPath("$.nodes[0].companyId").value(COMPANY_ID.toString()))
                .andExpect(jsonPath("$.nodes[0].abnormalReturns[0].windowDays").value(20))
                .andExpect(jsonPath("$.nodes[0].position").value("REPORTED"))
                .andExpect(jsonPath("$.nodes[0].positionLabel").value("기사에 나온 기업"))
                .andExpect(jsonPath("$.nodes[0].signals[0].horizon").value("SHORT"))
                .andExpect(jsonPath("$.nodes[0].signals[0].windowDays").value(3))
                .andExpect(jsonPath("$.nodes[0].signals[0].caseScope").value("POSITION"))
                .andExpect(jsonPath("$.nodes[0].signals[0].sameShare").value(0.714286))
                .andExpect(jsonPath("$.nodes[0].expectedMoves[0].windowDays").value(3))
                .andExpect(jsonPath("$.nodes[0].expectedMoves[0].expectedMove").value(0.031))
                .andExpect(jsonPath("$.nodes[0].expectedMoves[0].grade").value("LARGE"))
                .andExpect(jsonPath("$.nodes[0].fundamental.sizeClass").value("MID"))
                .andExpect(jsonPath("$.nodes[0].fundamental.priceDate").value("2026-10-16"))
                .andExpect(jsonPath("$.nodes[1].position").isEmpty())
                .andExpect(jsonPath("$.nodes[1].signals").isEmpty())
                .andExpect(jsonPath("$.nodes[1].expectedMoves").isEmpty())
                .andExpect(jsonPath("$.nodes[1].fundamental").isEmpty());
    }

    @Test
    void malformedEventIdIs400() throws Exception {
        mockMvc.perform(get("/api/events/not-a-uuid"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void chartRouteReturnsPoints() throws Exception {
        when(eventService.findChart(EVENT_ID, COMPANY_ID)).thenReturn(new EventChartResponse(EVENT_ID, COMPANY_ID,
                LocalDate.of(2026, 10, 13), "KOSDAQ", List.of(
                        new ChartPointResponse(-1, LocalDate.of(2026, 10, 12), new BigDecimal("10000"),
                                new BigDecimal("900"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO),
                        new ChartPointResponse(0, LocalDate.of(2026, 10, 13), null, new BigDecimal("909"), null,
                                new BigDecimal("0.01"), null))));

        mockMvc.perform(get("/api/events/{eventId}/companies/{companyId}/chart", EVENT_ID, COMPANY_ID))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.tradingDate").value("2026-10-13"))
                .andExpect(jsonPath("$.benchmark").value("KOSDAQ"))
                .andExpect(jsonPath("$.points[0].offset").value(-1))
                .andExpect(jsonPath("$.points[1].date").value("2026-10-13"))
                .andExpect(jsonPath("$.points[1].close").isEmpty())
                .andExpect(jsonPath("$.points[1].indexReturn").value(0.01));
    }

    @Test
    void backtestRouteIsNotCapturedByEventId() throws Exception {
        when(eventService.findBacktest("SEMICONDUCTOR")).thenReturn(new BacktestResponse("SEMICONDUCTOR",
                LocalDate.of(2026, 6, 30), LocalDate.of(2026, 7, 1), LocalDate.of(2026, 10, 14), List.of(
                        new BacktestRowResponse("SHORT", null, null, 100, 40, 24, new BigDecimal("0.600000"), 30, 10,
                                new BigDecimal("0.45"), new BigDecimal("0.55"))),
                List.of(new MoveBacktestRowResponse("SHORT", 3, "LARGE", 30, new BigDecimal("0.065"),
                        new BigDecimal("0.071")))));

        mockMvc.perform(get("/api/events/backtest").param("valueChain", "SEMICONDUCTOR"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.trainEnd").value("2026-06-30"))
                .andExpect(jsonPath("$.testStart").value("2026-07-01"))
                .andExpect(jsonPath("$.rows[0].horizon").value("SHORT"))
                .andExpect(jsonPath("$.rows[0].newsType").isEmpty())
                .andExpect(jsonPath("$.rows[0].hitRate").value(0.6))
                .andExpect(jsonPath("$.rows[0].baselineRate").value(0.55))
                .andExpect(jsonPath("$.moveRows[0].grade").value("LARGE"))
                .andExpect(jsonPath("$.moveRows[0].realizedMove").value(0.071));
        verify(eventService).findBacktest("SEMICONDUCTOR");
    }

    @Test
    void companyHistoryRouteIsNotCapturedByEventId() throws Exception {
        CompanyHistoryItemResponse item = new CompanyHistoryItemResponse(summary(), "REPORTED", "REPORTED",
                "기사에 나온 기업", List.of(signal()), List.of());
        when(eventService.findCompanyHistory(COMPANY_ID, null, 10))
                .thenReturn(CursorPageResponse.of(List.of(item), "next", true));

        mockMvc.perform(get("/api/events/company-history").param("companyId", COMPANY_ID.toString())
                        .param("size", "10"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].event.eventId").value(EVENT_ID.toString()))
                .andExpect(jsonPath("$.items[0].basis").value("REPORTED"))
                .andExpect(jsonPath("$.items[0].positionLabel").value("기사에 나온 기업"))
                .andExpect(jsonPath("$.items[0].signals[0].label").value("DOWN"))
                .andExpect(jsonPath("$.nextCursor").value("next"));
        verify(eventService).findCompanyHistory(COMPANY_ID, null, 10);
    }

    @Test
    void companyHistoryRequiresCompanyId() throws Exception {
        mockMvc.perform(get("/api/events/company-history"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
        mockMvc.perform(get("/api/events/company-history").param("companyId", "not-a-uuid"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
        verifyNoInteractions(eventService);
    }
}
