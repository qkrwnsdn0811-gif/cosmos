package com.cosmos.api.company.repository;

import com.cosmos.api.company.entity.CompanyAlias;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface CompanyAliasRepository extends JpaRepository<CompanyAlias, UUID> {
}
