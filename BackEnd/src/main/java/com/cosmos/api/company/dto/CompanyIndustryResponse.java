package com.cosmos.api.company.dto;

import com.cosmos.api.company.entity.CompanyIndustry;
import java.util.UUID;

public record CompanyIndustryResponse(
        UUID industryId,
        String name,
        boolean primary
) {

    public static CompanyIndustryResponse from(CompanyIndustry companyIndustry) {
        return new CompanyIndustryResponse(
                companyIndustry.getIndustry().getIndustryId(),
                companyIndustry.getIndustry().getName(),
                companyIndustry.isPrimary());
    }
}
