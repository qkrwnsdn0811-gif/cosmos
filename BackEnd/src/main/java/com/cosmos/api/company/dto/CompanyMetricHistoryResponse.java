package com.cosmos.api.company.dto;

import java.util.List;
import java.util.UUID;

public record CompanyMetricHistoryResponse(
        UUID companyId,
        String window,
        List<CompanyMetricHistoryItemResponse> items
) {

    public CompanyMetricHistoryResponse {
        items = List.copyOf(items);
    }
}
