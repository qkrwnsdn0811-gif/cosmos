package com.cosmos.api.event.controller;

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

/** NEXUS 이벤트(빅카인즈 기사 1건)와 그 근거·추정 관계 지도. 비로그인 공개 조회. */
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
            @RequestParam(defaultValue = "false") boolean hasInferred,
            @RequestParam(required = false) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size
    ) {
        return eventService.findEvents(valueChain, companyId, hasInferred, cursor, size);
    }

    @GetMapping("/{eventId}")
    public EventMapResponse findEventMap(@PathVariable UUID eventId) {
        return eventService.findEventMap(eventId);
    }
}
