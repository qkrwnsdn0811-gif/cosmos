package com.cosmos.api.graph.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.math.BigDecimal;

public record StockPriceChangeResponse(
        @JsonProperty("7D") BigDecimal sevenDays,
        @JsonProperty("30D") BigDecimal thirtyDays,
        @JsonProperty("90D") BigDecimal ninetyDays
) {
}
