package com.cosmos.api.user.dto;

import com.cosmos.api.user.repository.UserScrapQueryRepository.ScrapRow;
import java.time.Instant;
import java.util.UUID;

/** 내 스크랩 목록의 한 줄. 뉴스 요약 정보 + 스크랩 시각. */
public record NewsScrapItemResponse(
        UUID newsId,
        String title,
        String summary,
        String publisher,
        String originalUrl,
        Instant publishedAt,
        Instant scrappedAt
) {

    public static NewsScrapItemResponse from(ScrapRow row) {
        return new NewsScrapItemResponse(
                row.newsId(), row.title(), row.summary(), row.publisher(),
                row.originalUrl(), row.publishedAt(), row.scrappedAt());
    }
}
