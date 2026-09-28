package com.cosmos.api.company.dto;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public record StockPriceHistoryResponse(
        UUID companyId,
        String period,
        String interval,
        Instant asOfAt,
        List<StockPriceItemResponse> items
) {

    public StockPriceHistoryResponse {
        items = List.copyOf(items);
    }
}
