package com.cosmos.api.company.repository;

import com.cosmos.api.company.entity.CompanyIndustry;
import com.cosmos.api.company.entity.CompanyIndustryId;
import java.util.Collection;
import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface CompanyIndustryRepository extends JpaRepository<CompanyIndustry, CompanyIndustryId> {

    @Query("""
            select ci
            from CompanyIndustry ci
            join fetch ci.industry i
            where ci.company.companyId = :companyId
            order by ci.primary desc, i.name asc
            """)
    List<CompanyIndustry> findAllWithIndustryByCompanyId(@Param("companyId") UUID companyId);

    /** 여러 기업의 대표 산업을 한 번에 읽는다 (목록 화면에서 기업마다 따로 조회하지 않기 위해). */
    @Query("""
            select ci
            from CompanyIndustry ci
            join fetch ci.industry
            where ci.company.companyId in :companyIds
              and ci.primary = true
            """)
    List<CompanyIndustry> findPrimaryWithIndustryByCompanyIds(@Param("companyIds") Collection<UUID> companyIds);
}
