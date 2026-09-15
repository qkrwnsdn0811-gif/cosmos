package com.cosmos.api.company.dto;

import com.cosmos.api.company.entity.Company;
import java.util.List;
import java.util.Objects;
import java.util.UUID;

public record CompanyDetailResponse(
        UUID companyId,
        String name,
        String nameEn,
        String stockCode,
        String market,
        String description,
        List<CompanyIndustryResponse> industries,
        boolean watched
) {

    public CompanyDetailResponse {
        industries = List.copyOf(industries);
    }

    public static CompanyDetailResponse of(
            Company company,
            List<CompanyIndustryResponse> industries,
            boolean watched
    ) {
        return new CompanyDetailResponse(
                company.getCompanyId(),
                company.getName(),
                Objects.toString(company.getNameEn(), ""),
                Objects.toString(company.getStockCode(), ""),
                Objects.toString(company.getMarket(), ""),
                Objects.toString(company.getDescription(), ""),
                industries,
                watched);
    }
}
