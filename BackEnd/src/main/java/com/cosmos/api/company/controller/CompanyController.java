package com.cosmos.api.company.controller;

import com.cosmos.api.company.dto.CompanyDetailResponse;
import com.cosmos.api.company.dto.CompanyMetricHistoryResponse;
import com.cosmos.api.company.dto.CompanyMetricResponse;
import com.cosmos.api.company.dto.CompanySummaryResponse;
import com.cosmos.api.company.dto.IndustryListResponse;
import com.cosmos.api.company.dto.StockPriceHistoryResponse;
import com.cosmos.api.company.repository.CompanySearchCondition;
import com.cosmos.api.company.service.CompanyService;
import com.cosmos.api.global.response.CursorPageResponse;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import java.time.LocalDate;
import java.util.UUID;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@Validated
@RestController
@RequestMapping("/api")
public class CompanyController {

    private final CompanyService companyService;

    public CompanyController(CompanyService companyService) {
        this.companyService = companyService;
    }

    @GetMapping("/companies")
    public CursorPageResponse<CompanySummaryResponse> findCompanies(
            @RequestParam(required = false) @Size(max = 200) String keyword,
            @RequestParam(required = false) @Size(max = 30) String market,
            @RequestParam(required = false) UUID industryId,
            @RequestParam(required = false) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size
    ) {
        return companyService.findCompanies(
                new CompanySearchCondition(keyword, market, industryId, cursor, size));
    }

    @GetMapping("/companies/{companyId}")
    public CompanyDetailResponse findCompany(@PathVariable UUID companyId) {
        return companyService.findCompany(companyId);
    }

    @GetMapping("/companies/{companyId}/metrics")
    public CompanyMetricResponse findLatestMetric(
            @PathVariable UUID companyId,
            @RequestParam(defaultValue = "30D") String window
    ) {
        return companyService.findLatestMetric(companyId, window);
    }

    @GetMapping("/companies/{companyId}/metrics/history")
    public CompanyMetricHistoryResponse findMetricHistory(
            @PathVariable UUID companyId,
            @RequestParam(defaultValue = "30D") String window,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate from,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate to
    ) {
        return companyService.findMetricHistory(companyId, window, from, to);
    }

    @GetMapping("/companies/{companyId}/stock-prices")
    public StockPriceHistoryResponse findStockPrices(
            @PathVariable UUID companyId,
            @RequestParam(defaultValue = "1M") String period,
            @RequestParam(defaultValue = "1D") String interval
    ) {
        return companyService.findStockPrices(companyId, period, interval);
    }

    @GetMapping("/industries")
    public IndustryListResponse findIndustries() {
        return companyService.findIndustries();
    }
}
