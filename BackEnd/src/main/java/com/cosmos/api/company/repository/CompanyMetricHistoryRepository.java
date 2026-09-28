package com.cosmos.api.company.repository;

import com.cosmos.api.company.entity.CompanyMetricHistory;
import com.cosmos.api.company.entity.CompanyMetricHistoryId;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface CompanyMetricHistoryRepository
        extends JpaRepository<CompanyMetricHistory, CompanyMetricHistoryId> {

    Optional<CompanyMetricHistory> findFirstByCompanyCompanyIdAndWindowTypeOrderByMeasuredAtDesc(
            UUID companyId,
            String windowType
    );

    List<CompanyMetricHistory>
            findAllByCompanyCompanyIdAndWindowTypeAndMeasuredAtGreaterThanEqualAndMeasuredAtLessThanOrderByMeasuredAtAsc(
                    UUID companyId,
                    String windowType,
                    Instant fromInclusive,
                    Instant toExclusive
            );
}
