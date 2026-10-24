package com.cosmos.api.event.dto;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

/**
 * 이벤트(빅카인즈 기사 1건) 요약. newsId는 기존 /api/news/{newsId}와 같은 문서 ID다.
 * inferredCount는 추정·OK, holdCount는 추정·판단 보류 기업 수다.
 * newsType은 뉴스 유형 코드(분류 전이면 null), newsTypeLabel은 그 화면 이름, signalCounts는 투자 기간별 방향 딱지 수다.
 */
public record EventSummaryResponse(
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
        List<EventCompanyRefResponse> reportedCompanies,
        int inferredCount,
        int holdCount,
        String newsType,
        String newsTypeLabel,
        SignalCountsResponse signalCounts
) {
    public EventSummaryResponse {
        categories = List.copyOf(categories);
        reportedCompanies = List.copyOf(reportedCompanies);
        signalCounts = signalCounts == null ? SignalCountsResponse.empty() : signalCounts;
    }
}
