package com.cosmos.api.event.controller;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.event.dto.EventCompanyRefResponse;
import com.cosmos.api.event.dto.EventMapResponse;
import com.cosmos.api.event.dto.EventNodeResponse;
import com.cosmos.api.event.dto.EventSummaryResponse;
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
                List.of("경제>반도체"), List.of(new EventCompanyRefResponse(COMPANY_ID, "기업", "990001", "KOSDAQ")), 3, 1);
    }

    @Test
    void listPassesFiltersAndReturnsCamelCase() throws Exception {
        when(eventService.findEvents("SEMICONDUCTOR", null, true, null, 5))
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
        verify(eventService).findEvents("SEMICONDUCTOR", null, true, null, 5);
    }

    @Test
    void invalidCursorIs400() throws Exception {
        when(eventService.findEvents(null, null, false, "broken", 20))
                .thenThrow(new BusinessException(CommonErrorCode.INVALID_CURSOR));

        mockMvc.perform(get("/api/events").param("cursor", "broken"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_CURSOR"));
    }

    @Test
    void mapReturnsNodes() throws Exception {
        EventNodeResponse node = new EventNodeResponse(COMPANY_ID, "기업", "990001", "KOSDAQ", "REPORTED", 0,
                new BigDecimal("1.0"), "OK", null, null, null, null, List.of(), List.of());
        when(eventService.findEventMap(EVENT_ID)).thenReturn(new EventMapResponse(summary(), List.of(node)));

        mockMvc.perform(get("/api/events/{eventId}", EVENT_ID))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.event.tradingDate").value("2026-10-13"))
                .andExpect(jsonPath("$.nodes[0].basis").value("REPORTED"))
                .andExpect(jsonPath("$.nodes[0].companyId").value(COMPANY_ID.toString()));
    }

    @Test
    void malformedEventIdIs400() throws Exception {
        mockMvc.perform(get("/api/events/not-a-uuid"))
                .andExpect(status().isBadRequest());
    }
}
