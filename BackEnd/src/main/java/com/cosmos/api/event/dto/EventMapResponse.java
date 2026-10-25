package com.cosmos.api.event.dto;

import java.util.List;

/** 기사 1건의 관계 지도. nodes는 REPORTED 먼저(점수 내림차순), 그다음 INFERRED(점수 내림차순). */
public record EventMapResponse(
        EventSummaryResponse event,
        List<EventNodeResponse> nodes
) {
    public EventMapResponse {
        nodes = List.copyOf(nodes);
    }
}
