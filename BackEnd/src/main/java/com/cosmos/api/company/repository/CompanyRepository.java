package com.cosmos.api.company.repository;

import com.cosmos.api.company.entity.Company;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface CompanyRepository extends JpaRepository<Company, UUID> {

    @Query("select c from Company c where c.companyId = :companyId and c.status = 'ACTIVE'")
    Optional<Company> findActiveById(@Param("companyId") UUID companyId);
}
