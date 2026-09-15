package com.cosmos.api.company.service;

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
import com.cosmos.api.company.entity.Company;
import com.cosmos.api.company.entity.StockPriceHistory;
import com.cosmos.api.company.error.CompanyErrorCode;
import com.cosmos.api.company.repository.CompanyIndustryRepository;
import com.cosmos.api.company.repository.CompanyMetricHistoryRepository;
import com.cosmos.api.company.repository.CompanyQueryRepository;
import com.cosmos.api.company.repository.CompanyRepository;
import com.cosmos.api.company.repository.CompanySearchCondition;
import com.cosmos.api.company.repository.CompanySearchRow;
import com.cosmos.api.company.repository.StockPriceHistoryRepository;
import com.cosmos.api.company.type.MetricWindow;
import com.cosmos.api.company.type.StockInterval;
import com.cosmos.api.company.type.StockPeriod;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import com.cosmos.api.global.response.CursorPageResponse;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true)
public class CompanyService {

    private final CompanyRepository companyRepository;
    private final CompanyIndustryRepository companyIndustryRepository;
    private final CompanyQueryRepository companyQueryRepository;
    private final CompanyMetricHistoryRepository companyMetricHistoryRepository;
    private final StockPriceHistoryRepository stockPriceHistoryRepository;
    private final CompanyCursorCodec cursorCodec;
    private final Clock clock;

    public CompanyService(
            CompanyRepository companyRepository,
            CompanyIndustryRepository companyIndustryRepository,
            CompanyQueryRepository companyQueryRepository,
            CompanyMetricHistoryRepository companyMetricHistoryRepository,
            StockPriceHistoryRepository stockPriceHistoryRepository,
            CompanyCursorCodec cursorCodec,
            Clock clock
    ) {
        this.companyRepository = companyRepository;
        this.companyIndustryRepository = companyIndustryRepository;
        this.companyQueryRepository = companyQueryRepository;
        this.companyMetricHistoryRepository = companyMetricHistoryRepository;
        this.stockPriceHistoryRepository = stockPriceHistoryRepository;
        this.cursorCodec = cursorCodec;
        this.clock = clock;
    }

    public CursorPageResponse<CompanySummaryResponse> findCompanies(CompanySearchCondition condition) {
        CompanyCursor cursor = cursorCodec.decode(condition.cursor());
        List<CompanySearchRow> rows = companyQueryRepository.search(
                condition,
                cursor == null ? null : cursor.searchRank(),
                cursor == null ? null : cursor.companyName(),
                cursor == null ? null : cursor.companyId());

        boolean hasNext = rows.size() > condition.size();
        List<CompanySearchRow> pageRows = hasNext ? rows.subList(0, condition.size()) : rows;
        List<CompanySummaryResponse> items = pageRows.stream()
                .map(this::toSummary)
                .toList();

        String nextCursor = null;
        if (hasNext && !pageRows.isEmpty()) {
            CompanySearchRow last = pageRows.getLast();
            nextCursor = cursorCodec.encode(new CompanyCursor(
                    last.searchRank(),
                    last.name(),
                    last.companyId()));
        }

        return CursorPageResponse.of(items, nextCursor, hasNext);
    }

    public CompanyDetailResponse findCompany(UUID companyId) {
        Company company = companyRepository.findActiveById(companyId)
                .orElseThrow(() -> new BusinessException(CompanyErrorCode.COMPANY_NOT_FOUND));
        List<CompanyIndustryResponse> industries = companyIndustryRepository
                .findAllWithIndustryByCompanyId(companyId).stream()
                .map(CompanyIndustryResponse::from)
                .toList();

        return CompanyDetailResponse.of(company, industries, false);
    }

    public IndustryListResponse findIndustries() {
        List<IndustryResponse> items = companyQueryRepository.findIndustries().stream()
                .map(row -> new IndustryResponse(
                        row.industryId(),
                        row.parentIndustryId(),
                        row.name(),
                        Objects.toString(row.description(), ""),
                        row.companyCount()))
                .toList();
        return new IndustryListResponse(items);
    }

    public CompanyMetricResponse findLatestMetric(UUID companyId, String windowValue) {
        ensureActiveCompany(companyId);
        MetricWindow window = MetricWindow.from(windowValue);

        return companyMetricHistoryRepository
                .findFirstByCompanyCompanyIdAndWindowTypeOrderByMeasuredAtDesc(companyId, window.code())
                .map(metric -> CompanyMetricResponse.from(companyId, window.code(), metric))
                .orElseGet(() -> CompanyMetricResponse.empty(companyId, window.code()));
    }

    public CompanyMetricHistoryResponse findMetricHistory(
            UUID companyId,
            String windowValue,
            LocalDate from,
            LocalDate to
    ) {
        ensureActiveCompany(companyId);
        MetricWindow window = MetricWindow.from(windowValue);
        LocalDate resolvedTo = to == null ? LocalDate.now(clock) : to;
        LocalDate resolvedFrom = from == null
                ? resolvedTo.minusDays(window.days() - 1L)
                : from;
        if (resolvedFrom.isAfter(resolvedTo)) {
            throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
        }

        Instant fromInclusive = resolvedFrom.atStartOfDay(ZoneOffset.UTC).toInstant();
        Instant toExclusive = resolvedTo.plusDays(1).atStartOfDay(ZoneOffset.UTC).toInstant();
        List<CompanyMetricHistoryItemResponse> items = companyMetricHistoryRepository
                .findAllByCompanyCompanyIdAndWindowTypeAndMeasuredAtGreaterThanEqualAndMeasuredAtLessThanOrderByMeasuredAtAsc(
                        companyId,
                        window.code(),
                        fromInclusive,
                        toExclusive).stream()
                .map(CompanyMetricHistoryItemResponse::from)
                .toList();

        return new CompanyMetricHistoryResponse(companyId, window.code(), items);
    }

    public StockPriceHistoryResponse findStockPrices(
            UUID companyId,
            String periodValue,
            String intervalValue
    ) {
        ensureActiveCompany(companyId);
        StockPeriod period = StockPeriod.from(periodValue);
        StockInterval interval = StockInterval.from(intervalValue);

        StockPriceHistory latest = stockPriceHistoryRepository
                .findFirstByCompanyCompanyIdAndIntervalTypeOrderByTradingAtDesc(companyId, interval.code())
                .orElse(null);
        if (latest == null) {
            return new StockPriceHistoryResponse(
                    companyId, period.code(), interval.code(), null, List.of());
        }

        Instant asOfAt = latest.getTradingAt();
        Instant fromInclusive = period.subtractFrom(asOfAt.atZone(ZoneOffset.UTC)).toInstant();
        List<StockPriceItemResponse> items = stockPriceHistoryRepository
                .findAllByCompanyCompanyIdAndIntervalTypeAndTradingAtGreaterThanEqualAndTradingAtLessThanEqualOrderByTradingAtAsc(
                        companyId,
                        interval.code(),
                        fromInclusive,
                        asOfAt).stream()
                .map(StockPriceItemResponse::from)
                .toList();

        return new StockPriceHistoryResponse(
                companyId, period.code(), interval.code(), asOfAt, items);
    }

    private void ensureActiveCompany(UUID companyId) {
        if (companyRepository.findActiveById(companyId).isEmpty()) {
            throw new BusinessException(CompanyErrorCode.COMPANY_NOT_FOUND);
        }
    }

    private CompanySummaryResponse toSummary(CompanySearchRow row) {
        return new CompanySummaryResponse(
                row.companyId(),
                row.name(),
                Objects.toString(row.nameEn(), ""),
                Objects.toString(row.stockCode(), ""),
                Objects.toString(row.market(), ""),
                new IndustryReferenceResponse(row.primaryIndustryId(), row.primaryIndustryName()),
                false);
    }
}
