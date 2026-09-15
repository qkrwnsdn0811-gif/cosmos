package com.cosmos.api.company.dto;

import com.cosmos.api.company.entity.StockPriceHistory;
import java.math.BigDecimal;
import java.time.Instant;

public record StockPriceItemResponse(
        Instant tradingAt,
        BigDecimal openPrice,
        BigDecimal highPrice,
        BigDecimal lowPrice,
        BigDecimal closePrice,
        long tradingVolume
) {

    public static StockPriceItemResponse from(StockPriceHistory price) {
        return new StockPriceItemResponse(
                price.getTradingAt(),
                price.getOpenPrice(),
                price.getHighPrice(),
                price.getLowPrice(),
                price.getClosePrice(),
                price.getTradingVolume());
    }
}
