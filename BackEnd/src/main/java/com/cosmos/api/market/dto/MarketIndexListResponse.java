package com.cosmos.api.market.dto;

import java.time.LocalDate;
import java.util.List;

/** asOf 는 지수가 있는 마지막 거래일. items 는 KOSPI, KOSDAQ 순. */
public record MarketIndexListResponse(LocalDate asOf, List<MarketIndexResponse> items) {
}
