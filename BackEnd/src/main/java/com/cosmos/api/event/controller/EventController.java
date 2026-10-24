package com.cosmos.api.event.controller;

import com.cosmos.api.event.dto.BacktestResponse;
import com.cosmos.api.event.dto.CompanyHistoryItemResponse;
import com.cosmos.api.event.dto.EventChartResponse;
import com.cosmos.api.event.dto.EventMapResponse;
import com.cosmos.api.event.dto.EventSummaryResponse;
import com.cosmos.api.event.service.EventService;
import com.cosmos.api.global.response.CursorPageResponse;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import java.util.UUID;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * NEXUS 이벤트(빅카인즈 기사 1건)와 그 근거·추정 관계 지도. 비로그인 공개 조회.
 * /backtest, /company-history 같은 고정 경로는 Spring MVC가 /{eventId}보다 먼저 고른다(더 구체적인 패턴 우선).
 */
@Validated
@RestController
@RequestMapping("/api/events")
public class EventController {

    private final EventService eventService;

    public EventController(EventService eventService) {
        this.eventService = eventService;
    }

    @GetMapping
    public CursorPageResponse<EventSummaryResponse> findEvents(
            @RequestParam(required = false) String valueChain,
            @RequestParam(required = false) UUID companyId,
            @RequestParam(required = false) String newsType,
            @RequestParam(defaultValue = "false") boolean hasInferred,
            @RequestParam(required = false) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size
    ) {
        return eventService.findEvents(valueChain, companyId, newsType, hasInferred, cursor, size);
    }

    /** 방향 딱지 적중률(학습 기간 뒤 뉴스로 잰 값). */
    @GetMapping("/backtest")
    public BacktestResponse findBacktest(@RequestParam(required = false) String valueChain) {
        return eventService.findBacktest(valueChain);
    }

    /** 기업 한 곳의 과거 뉴스와 그때 주가 반응. */
    @GetMapping("/company-history")
    public CursorPageResponse<CompanyHistoryItemResponse> findCompanyHistory(
            @RequestParam UUID companyId,
            @RequestParam(required = false) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size
    ) {
        return eventService.findCompanyHistory(companyId, cursor, size);
    }

    @GetMapping("/{eventId}")
    public EventMapResponse findEventMap(@PathVariable UUID eventId) {
        return eventService.findEventMap(eventId);
    }

    /** 뉴스 전후 주가 차트(이벤트 거래일 앞 5 ~ 뒤 20거래일, 시장 지수 대비). */
    @GetMapping("/{eventId}/companies/{companyId}/chart")
    public EventChartResponse findChart(@PathVariable UUID eventId, @PathVariable UUID companyId) {
        return eventService.findChart(eventId, companyId);
    }
}
