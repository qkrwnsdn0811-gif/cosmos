package com.cosmos.api.market.dto;

import java.math.BigDecimal;
import java.time.LocalDate;

public record IndexPointResponse(LocalDate date, BigDecimal close) {
}
