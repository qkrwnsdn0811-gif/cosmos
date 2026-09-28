package com.cosmos.api.user.dto;

import com.cosmos.api.company.dto.IndustryReferenceResponse;
import com.cosmos.api.company.entity.Company;
import com.cosmos.api.company.entity.Industry;
import com.cosmos.api.user.entity.UserWatchCompany;
import java.time.Instant;
import java.util.UUID;

/**
 * 관심 기업 목록의 한 줄. 기업 기본 정보 + 대표 산업 + 등록 시각.
 *
 * @param primaryIndustry 대표 산업이 지정되지 않은 기업이면 null
 */
public record WatchCompanyItemResponse(
        UUID companyId,
        String name,
        String stockCode,
        String market,
        IndustryReferenceResponse primaryIndustry,
        Instant watchedAt
) {

    public static WatchCompanyItemResponse from(UserWatchCompany watch, Industry primaryIndustry) {
        Company company = watch.getCompany();
        IndustryReferenceResponse industry = primaryIndustry == null
                ? null
                : new IndustryReferenceResponse(primaryIndustry.getIndustryId(), primaryIndustry.getName());
        return new WatchCompanyItemResponse(
                company.getCompanyId(),
                company.getName(),
                company.getStockCode(),
                company.getMarket(),
                industry,
                watch.getCreatedAt());
    }
}
