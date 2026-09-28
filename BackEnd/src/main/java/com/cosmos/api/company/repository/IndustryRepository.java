package com.cosmos.api.company.repository;

import com.cosmos.api.company.entity.Industry;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface IndustryRepository extends JpaRepository<Industry, UUID> {
}
