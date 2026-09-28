package com.cosmos.api.company.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.cosmos.api.company.dto.CompanyDetailResponse;
import com.cosmos.api.company.dto.CompanySummaryResponse;
import com.cosmos.api.company.dto.IndustryListResponse;
import com.cosmos.api.company.error.CompanyErrorCode;
import com.cosmos.api.company.repository.CompanySearchCondition;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.response.CursorPageResponse;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

@SpringBootTest
@Transactional
class CompanyServiceIntegrationTest {

    private static final UUID PARENT_INDUSTRY_ID = UUID.fromString("10000000-0000-0000-0000-000000000001");
    private static final UUID INDUSTRY_ID = UUID.fromString("10000000-0000-0000-0000-000000000002");
    private static final UUID EMPTY_INDUSTRY_ID = UUID.fromString("10000000-0000-0000-0000-000000000003");
    private static final UUID ALPHA_ID = UUID.fromString("20000000-0000-0000-0000-000000000001");
    private static final UUID BETA_ID = UUID.fromString("20000000-0000-0000-0000-000000000002");
    private static final UUID INACTIVE_ID = UUID.fromString("20000000-0000-0000-0000-000000000003");

    @Autowired
    private CompanyService companyService;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @BeforeEach
    void setUp() {
        jdbcTemplate.update("INSERT INTO industry (industry_id, name, description) VALUES (?, ?, ?)",
                PARENT_INDUSTRY_ID, "테스트상위산업", "상위 산업");
        jdbcTemplate.update("INSERT INTO industry (industry_id, parent_industry_id, name, description) VALUES (?, ?, ?, ?)",
                INDUSTRY_ID, PARENT_INDUSTRY_ID, "테스트하위산업", "하위 산업");
        jdbcTemplate.update("INSERT INTO industry (industry_id, name) VALUES (?, ?)",
                EMPTY_INDUSTRY_ID, "테스트빈산업");

        insertCompany(ALPHA_ID, "테스트알파", "Test Alpha", "T00001", "TEST", "ACTIVE");
        insertCompany(BETA_ID, "테스트베타", "Test Beta", "T00002", "TEST", "ACTIVE");
        insertCompany(INACTIVE_ID, "테스트중지", "Test Inactive", "T00003", "TEST", "INACTIVE");

        jdbcTemplate.update("INSERT INTO company_alias (alias_id, company_id, alias_name, alias_type, normalized_name) VALUES (?, ?, ?, ?, ?)",
                UUID.fromString("30000000-0000-0000-0000-000000000001"), ALPHA_ID,
                "테스트별칭", "FORMER_NAME", "테스트별칭");
        jdbcTemplate.update("INSERT INTO company_industry (company_id, industry_id, is_primary) VALUES (?, ?, TRUE)",
                ALPHA_ID, INDUSTRY_ID);
        jdbcTemplate.update("INSERT INTO company_industry (company_id, industry_id, is_primary) VALUES (?, ?, TRUE)",
                BETA_ID, INDUSTRY_ID);
        jdbcTemplate.update("INSERT INTO company_industry (company_id, industry_id, is_primary) VALUES (?, ?, TRUE)",
                INACTIVE_ID, INDUSTRY_ID);
        jdbcTemplate.update("INSERT INTO company_industry (company_id, industry_id, is_primary) VALUES (?, ?, FALSE)",
                ALPHA_ID, PARENT_INDUSTRY_ID);
    }

    @Test
    void searchesByKoreanEnglishStockCodeAndAlias() {
        assertSingleCompany("알파", ALPHA_ID);
        assertSingleCompany("alpha", ALPHA_ID);
        assertSingleCompany("T00001", ALPHA_ID);
        assertSingleCompany("테스트별칭", ALPHA_ID);
    }

    @Test
    void filtersByMarketAndIndustryAndExcludesInactiveCompany() {
        CursorPageResponse<CompanySummaryResponse> result = companyService.findCompanies(
                new CompanySearchCondition(null, "test", INDUSTRY_ID, null, 20));

        assertThat(result.items()).extracting(CompanySummaryResponse::companyId)
                .containsExactlyInAnyOrder(ALPHA_ID, BETA_ID);
        assertThat(result.items()).allSatisfy(item -> {
            assertThat(item.primaryIndustry().industryId()).isEqualTo(INDUSTRY_ID);
            assertThat(item.watched()).isFalse();
        });
    }

    @Test
    void continuesCompanyListWithOpaqueCursor() {
        CompanySearchCondition firstCondition = new CompanySearchCondition(null, "TEST", INDUSTRY_ID, null, 1);
        CursorPageResponse<CompanySummaryResponse> first = companyService.findCompanies(firstCondition);

        CursorPageResponse<CompanySummaryResponse> second = companyService.findCompanies(
                new CompanySearchCondition(null, "TEST", INDUSTRY_ID, first.nextCursor(), 1));

        assertThat(first.items()).hasSize(1);
        assertThat(first.hasNext()).isTrue();
        assertThat(first.nextCursor()).isNotBlank();
        assertThat(second.items()).hasSize(1);
        assertThat(second.items().getFirst().companyId()).isNotEqualTo(first.items().getFirst().companyId());
        assertThat(second.hasNext()).isFalse();
        assertThat(second.nextCursor()).isNull();
    }

    @Test
    void returnsCompanyDetailWithPrimaryIndustryFirst() {
        CompanyDetailResponse result = companyService.findCompany(ALPHA_ID);

        assertThat(result.companyId()).isEqualTo(ALPHA_ID);
        assertThat(result.industries()).extracting(item -> item.industryId())
                .containsExactly(INDUSTRY_ID, PARENT_INDUSTRY_ID);
        assertThat(result.industries().getFirst().primary()).isTrue();
        assertThat(result.watched()).isFalse();
    }

    @Test
    void rejectsMissingOrInactiveCompanyDetail() {
        assertCompanyNotFound(UUID.fromString("20000000-0000-0000-0000-000000000099"));
        assertCompanyNotFound(INACTIVE_ID);
    }

    @Test
    void returnsIndustryHierarchyAndActiveCompanyCountIncludingZero() {
        IndustryListResponse result = companyService.findIndustries();

        var child = result.items().stream()
                .filter(item -> item.industryId().equals(INDUSTRY_ID))
                .findFirst()
                .orElseThrow();
        var empty = result.items().stream()
                .filter(item -> item.industryId().equals(EMPTY_INDUSTRY_ID))
                .findFirst()
                .orElseThrow();

        assertThat(child.parentIndustryId()).isEqualTo(PARENT_INDUSTRY_ID);
        assertThat(child.companyCount()).isEqualTo(2);
        assertThat(empty.companyCount()).isZero();
    }

    private void assertSingleCompany(String keyword, UUID expectedCompanyId) {
        CursorPageResponse<CompanySummaryResponse> result = companyService.findCompanies(
                new CompanySearchCondition(keyword, null, null, null, 20));

        assertThat(result.items()).extracting(CompanySummaryResponse::companyId)
                .containsExactly(expectedCompanyId);
    }

    private void assertCompanyNotFound(UUID companyId) {
        assertThatThrownBy(() -> companyService.findCompany(companyId))
                .isInstanceOf(BusinessException.class)
                .satisfies(exception -> assertThat(((BusinessException) exception).getErrorCode())
                        .isEqualTo(CompanyErrorCode.COMPANY_NOT_FOUND));
    }

    private void insertCompany(
            UUID companyId,
            String name,
            String nameEn,
            String stockCode,
            String market,
            String status
    ) {
        jdbcTemplate.update("""
                        INSERT INTO company
                            (company_id, name, name_en, stock_code, market, description, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                companyId, name, nameEn, stockCode, market, name + " 설명", status);
    }
}
