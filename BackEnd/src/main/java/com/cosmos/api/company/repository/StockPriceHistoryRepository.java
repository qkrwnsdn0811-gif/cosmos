package com.cosmos.api.company.repository;

import com.cosmos.api.company.entity.StockPriceHistory;
import com.cosmos.api.company.entity.StockPriceHistoryId;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface StockPriceHistoryRepository
        extends JpaRepository<StockPriceHistory, StockPriceHistoryId> {

    Optional<StockPriceHistory> findFirstByCompanyCompanyIdAndIntervalTypeOrderByTradingAtDesc(
            UUID companyId,
            String intervalType
    );

    List<StockPriceHistory>
            findAllByCompanyCompanyIdAndIntervalTypeAndTradingAtGreaterThanEqualAndTradingAtLessThanEqualOrderByTradingAtAsc(
                    UUID companyId,
                    String intervalType,
                    Instant fromInclusive,
                    Instant toInclusive
            );
}
