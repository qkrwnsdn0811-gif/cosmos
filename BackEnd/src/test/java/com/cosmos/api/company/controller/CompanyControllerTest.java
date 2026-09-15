package com.cosmos.api.company.controller;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.company.dto.CompanyDetailResponse;
import com.cosmos.api.company.dto.CompanyIndustryResponse;
import com.cosmos.api.company.dto.CompanyMetricHistoryItemResponse;
import com.cosmos.api.company.dto.CompanyMetricHistoryResponse;
import com.cosmos.api.company.dto.CompanyMetricResponse;
import com.cosmos.api.company.dto.CompanySummaryResponse;
import com.cosmos.api.company.dto.IndustryListResponse;
import com.cosmos.api.company.dto.IndustryReferenceResponse;
import com.cosmos.api.company.dto.IndustryResponse;
import com.cosmos.api.company.dto.StockPriceHistoryResponse;
import com.cosmos.api.company.dto.StockPriceItemResponse;
import com.cosmos.api.company.repository.CompanySearchCondition;
import com.cosmos.api.company.service.CompanyService;
import com.cosmos.api.global.error.GlobalExceptionHandler;
import com.cosmos.api.global.response.CursorPageResponse;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class CompanyControllerTest {

    private static final UUID COMPANY_ID = UUID.fromString("20000000-0000-0000-0000-000000000001");
    private static final UUID INDUSTRY_ID = UUID.fromString("10000000-0000-0000-0000-000000000001");

    private CompanyService companyService;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        companyService = mock(CompanyService.class);
        mockMvc = MockMvcBuilders.standaloneSetup(new CompanyController(companyService))
                .setControllerAdvice(new GlobalExceptionHandler())
                .build();
    }

    @Test
    void returnsCompanyCursorPageWithoutSuccessWrapper() throws Exception {
        CompanySummaryResponse company = new CompanySummaryResponse(
                COMPANY_ID,
                "테스트기업",
                "Test Company",
                "T00001",
                "KOSPI",
                new IndustryReferenceResponse(INDUSTRY_ID, "반도체"),
                false);
        when(companyService.findCompanies(any()))
                .thenReturn(CursorPageResponse.of(List.of(company), "next-token", true));

        mockMvc.perform(get("/api/companies")
                        .param("keyword", "테스트")
                        .param("market", "kospi")
                        .param("industryId", INDUSTRY_ID.toString())
                        .param("cursor", "cursor-token")
                        .param("size", "10"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].companyId").value(COMPANY_ID.toString()))
                .andExpect(jsonPath("$.items[0].primaryIndustry.industryId").value(INDUSTRY_ID.toString()))
                .andExpect(jsonPath("$.items[0].watched").value(false))
                .andExpect(jsonPath("$.nextCursor").value("next-token"))
                .andExpect(jsonPath("$.hasNext").value(true));

        ArgumentCaptor<CompanySearchCondition> captor = ArgumentCaptor.forClass(CompanySearchCondition.class);
        verify(companyService).findCompanies(captor.capture());
        CompanySearchCondition condition = captor.getValue();
        org.assertj.core.api.Assertions.assertThat(condition.keyword()).isEqualTo("테스트");
        org.assertj.core.api.Assertions.assertThat(condition.market()).isEqualTo("KOSPI");
        org.assertj.core.api.Assertions.assertThat(condition.industryId()).isEqualTo(INDUSTRY_ID);
        org.assertj.core.api.Assertions.assertThat(condition.cursor()).isEqualTo("cursor-token");
        org.assertj.core.api.Assertions.assertThat(condition.size()).isEqualTo(10);
    }

    @Test
    void returnsCompanyDetail() throws Exception {
        when(companyService.findCompany(COMPANY_ID)).thenReturn(new CompanyDetailResponse(
                COMPANY_ID,
                "테스트기업",
                "Test Company",
                "T00001",
                "KOSPI",
                "기업 설명",
                List.of(new CompanyIndustryResponse(INDUSTRY_ID, "반도체", true)),
                false));

        mockMvc.perform(get("/api/companies/{companyId}", COMPANY_ID))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.companyId").value(COMPANY_ID.toString()))
                .andExpect(jsonPath("$.industries[0].primary").value(true));
    }

    @Test
    void returnsIndustryList() throws Exception {
        when(companyService.findIndustries()).thenReturn(new IndustryListResponse(List.of(
                new IndustryResponse(INDUSTRY_ID, null, "반도체", "산업 설명", 2))));

        mockMvc.perform(get("/api/industries"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].industryId").value(INDUSTRY_ID.toString()))
                .andExpect(jsonPath("$.items[0].companyCount").value(2));
    }

    @Test
    void returnsLatestMetric() throws Exception {
        when(companyService.findLatestMetric(COMPANY_ID, "30D")).thenReturn(new CompanyMetricResponse(
                COMPANY_ID, "30D", 152, 91, 23, new BigDecimal("0.620000"), 18,
                Instant.parse("2026-09-07T06:00:00Z")));

        mockMvc.perform(get("/api/companies/{companyId}/metrics", COMPANY_ID))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.window").value("30D"))
                .andExpect(jsonPath("$.newsMentionCount").value(152))
                .andExpect(jsonPath("$.measuredAt").value("2026-09-07T06:00:00Z"));
    }

    @Test
    void returnsMetricHistory() throws Exception {
        when(companyService.findMetricHistory(
                COMPANY_ID,
                "7D",
                LocalDate.parse("2026-09-01"),
                LocalDate.parse("2026-09-07")))
                .thenReturn(new CompanyMetricHistoryResponse(
                        COMPANY_ID,
                        "7D",
                        List.of(new CompanyMetricHistoryItemResponse(
                                Instant.parse("2026-09-07T00:00:00Z"),
                                40, 25, 5, new BigDecimal("0.700000"), 6))));

        mockMvc.perform(get("/api/companies/{companyId}/metrics/history", COMPANY_ID)
                        .param("window", "7D")
                        .param("from", "2026-09-01")
                        .param("to", "2026-09-07"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.window").value("7D"))
                .andExpect(jsonPath("$.items[0].relationshipCount").value(6));
    }

    @Test
    void returnsStockPriceHistory() throws Exception {
        Instant tradingAt = Instant.parse("2026-09-06T00:00:00Z");
        when(companyService.findStockPrices(COMPANY_ID, "1M", "1D"))
                .thenReturn(new StockPriceHistoryResponse(
                        COMPANY_ID,
                        "1M",
                        "1D",
                        tradingAt,
                        List.of(new StockPriceItemResponse(
                                tradingAt,
                                new BigDecimal("72000"),
                                new BigDecimal("73500"),
                                new BigDecimal("71500"),
                                new BigDecimal("73000"),
                                18234567L))));

        mockMvc.perform(get("/api/companies/{companyId}/stock-prices", COMPANY_ID))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.period").value("1M"))
                .andExpect(jsonPath("$.interval").value("1D"))
                .andExpect(jsonPath("$.items[0].closePrice").value(73000));
    }
}
