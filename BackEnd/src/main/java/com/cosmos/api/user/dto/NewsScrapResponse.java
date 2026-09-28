package com.cosmos.api.user.dto;

import com.cosmos.api.user.entity.UserScrap;
import java.time.Instant;
import java.util.UUID;

/** 뉴스 스크랩 등록 응답. 어떤 뉴스를 언제 스크랩했는지만 알려준다. */
public record NewsScrapResponse(
        UUID newsId,
        Instant scrappedAt
) {

    public static NewsScrapResponse from(UserScrap scrap) {
        return new NewsScrapResponse(scrap.getId().getDocumentId(), scrap.getCreatedAt());
    }
}
