package com.cosmos.api.company.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import java.io.Serializable;
import java.time.Instant;
import java.util.UUID;
import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.EqualsAndHashCode;
import lombok.NoArgsConstructor;

@Embeddable
@EqualsAndHashCode
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor
public class StockPriceHistoryId implements Serializable {

    @Column(name = "company_id", nullable = false)
    private UUID companyId;

    @Column(name = "trading_at", nullable = false)
    private Instant tradingAt;

    @Column(name = "interval_type", nullable = false, length = 20)
    private String intervalType;
}
