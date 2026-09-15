package com.cosmos.api.company.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import java.util.UUID;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Entity
@Table(name = "company_alias")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class CompanyAlias {

    @Id
    @Column(name = "alias_id", nullable = false)
    private UUID aliasId;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "company_id", nullable = false)
    private Company company;

    @Column(name = "alias_name", nullable = false, length = 200)
    private String aliasName;

    @Column(name = "alias_type", nullable = false, length = 50)
    private String aliasType;

    @Column(name = "normalized_name", nullable = false, length = 200)
    private String normalizedName;
}
