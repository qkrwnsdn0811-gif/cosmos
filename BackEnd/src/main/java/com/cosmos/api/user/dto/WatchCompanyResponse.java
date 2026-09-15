package com.cosmos.api.user.dto;

import com.cosmos.api.user.entity.UserWatchCompany;
import java.time.Instant;
import java.util.UUID;

/** 관심 기업 등록 응답. 어떤 기업을 언제 등록했는지만 알려준다. */
public record WatchCompanyResponse(
        UUID companyId,
        Instant watchedAt
) {

    public static WatchCompanyResponse from(UserWatchCompany watch) {
        return new WatchCompanyResponse(watch.getId().getCompanyId(), watch.getCreatedAt());
    }
}
