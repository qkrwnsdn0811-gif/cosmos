package com.cosmos.api.company.service;

import com.cosmos.api.company.dto.CompanyQuoteListResponse;
import com.cosmos.api.company.dto.CompanyQuoteResponse;
import com.cosmos.api.company.dto.RelatedCompanyListResponse;
import com.cosmos.api.company.dto.RelatedCompanyResponse;
import com.cosmos.api.company.error.CompanyErrorCode;
import com.cosmos.api.company.repository.CompanyQuoteQueryRepository;
import com.cosmos.api.company.repository.CompanyQuoteQueryRepository.CompanyRow;
import com.cosmos.api.company.repository.CompanyQuoteQueryRepository.DailyClose;
import com.cosmos.api.company.repository.CompanyQuoteQueryRepository.NewsCount;
import com.cosmos.api.company.repository.CompanyQuoteQueryRepository.YearRange;
import com.cosmos.api.company.repository.CompanyQuoteQueryRepository.RelationRow;
import com.cosmos.api.global.error.BusinessException;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** 기업 탭 시세판: 전 종목 시세와 관계 기업 시세. 등락은 거래일 수로 센다(1일 · 20 · 60). */
@Service
@Transactional(readOnly = true)
public class CompanyQuoteService {

    static final String RELATION_WINDOW = "1Y";
    static final int MONTH_DAYS = 20;
    static final int QUARTER_DAYS = 60;
    /** 미니 차트: 1개월 등락의 기준점부터 마지막 거래일까지 */
    static final int SPARK_SIZE = MONTH_DAYS + 1;
    static final int YEAR_DAYS = 250;
    /** 평소 뉴스: 최근 7일 앞의 60일을 7일 단위로 */
    static final double USUAL_WEEKS = 60.0 / 7;

    private final CompanyQuoteQueryRepository quoteRepository;

    public CompanyQuoteService(CompanyQuoteQueryRepository quoteRepository) {
        this.quoteRepository = quoteRepository;
    }

    public CompanyQuoteListResponse findQuotes() {
        List<CompanyQuoteResponse> items = new ArrayList<>(quotesById().values());
        items.sort(Comparator.comparing(CompanyQuoteResponse::marketCap, Comparator.nullsLast(Comparator.reverseOrder()))
                .thenComparing(CompanyQuoteResponse::name));
        LocalDate asOf = items.stream().map(CompanyQuoteResponse::tradingDate).filter(Objects::nonNull)
                .max(Comparator.naturalOrder()).orElse(null);
        return new CompanyQuoteListResponse(asOf, items);
    }

    /** 기업 → 시세. 관계 기업 시세(findRelated)도 이걸 쓴다 */
    public Map<UUID, CompanyQuoteResponse> quotesById() {
        Map<UUID, List<DailyClose>> closes = quoteRepository.findRecentDailyCloses(QUARTER_DAYS + 1);
        Map<UUID, Integer> relations = quoteRepository.findRelationCounts(RELATION_WINDOW);
        Map<UUID, NewsCount> news = quoteRepository.findNewsCounts();
        Map<UUID, YearRange> ranges = quoteRepository.findYearRanges(YEAR_DAYS);
        Map<UUID, CompanyQuoteResponse> result = new LinkedHashMap<>();
        for (CompanyRow row : quoteRepository.findActiveCompanies()) {
            result.put(row.companyId(), quote(row, closes.getOrDefault(row.companyId(), List.of()),
                    relations.getOrDefault(row.companyId(), 0), news.getOrDefault(row.companyId(), new NewsCount(0, 0)),
                    ranges.get(row.companyId())));
        }
        return result;
    }

    /** 관계 기업 시세. 상대별로 점수가 가장 높은 관계가 대표(관계 행은 점수 내림차순으로 온다) */
    public RelatedCompanyListResponse findRelated(UUID companyId) {
        Map<UUID, CompanyQuoteResponse> quotes = quotesById();
        if (!quotes.containsKey(companyId)) {
            throw new BusinessException(CompanyErrorCode.COMPANY_NOT_FOUND);
        }
        Map<UUID, RelationRow> best = new LinkedHashMap<>();
        Map<UUID, List<String>> others = new LinkedHashMap<>();
        for (RelationRow row : quoteRepository.findRelations(companyId, RELATION_WINDOW)) {
            if (!quotes.containsKey(row.otherId())) {
                continue;
            }
            RelationRow top = best.putIfAbsent(row.otherId(), row);
            if (top != null && !top.relationType().equals(row.relationType())) {
                List<String> rest = others.computeIfAbsent(row.otherId(), k -> new ArrayList<>());
                if (!rest.contains(row.relationType())) {
                    rest.add(row.relationType());
                }
            }
        }
        List<RelatedCompanyResponse> items = best.values().stream()
                .map(r -> new RelatedCompanyResponse(r.relationType(), direction(r), r.score(),
                        others.getOrDefault(r.otherId(), List.of()), quotes.get(r.otherId())))
                .toList();
        return new RelatedCompanyListResponse(companyId, RELATION_WINDOW, items);
    }

    private static String direction(RelationRow row) {
        if (!row.directed()) {
            return "NONE";
        }
        return row.outgoing() ? "OUT" : "IN";
    }

    /** recent 는 최근 것부터 */
    private static CompanyQuoteResponse quote(CompanyRow row, List<DailyClose> recent, int relationCount, NewsCount news,
                                              YearRange range) {
        BigDecimal close = recent.isEmpty() ? null : recent.get(0).close();
        BigDecimal prevClose = recent.size() > 1 ? recent.get(1).close() : null;
        LocalDate tradingDate = recent.isEmpty() ? null : recent.get(0).date();
        List<BigDecimal> spark = new ArrayList<>();
        for (int i = Math.min(SPARK_SIZE, recent.size()) - 1; i >= 0; i--) {
            spark.add(recent.get(i).close());
        }
        return new CompanyQuoteResponse(row.companyId(), row.name(), row.stockCode(), row.market(), row.industry(),
                close, prevClose, tradingDate, change(recent, 1), change(recent, MONTH_DAYS), change(recent, QUARTER_DAYS),
                spark, row.marketCap(), row.marketCapRank(), row.sizeClass(), row.per(), row.pbr(), row.dividendYield(),
                row.dividendPerShare(), row.netIncomeTtm(), row.equity(), row.fiscalBasis(), row.priceDate(),
                relationCount, news.recent(), news.before() / USUAL_WEEKS, range == null ? null : range.high(),
                range == null ? null : range.low());
    }

    /** back 거래일 전 종가 대비 비율, 모자라면 null */
    private static Double change(List<DailyClose> recent, int back) {
        if (recent.size() <= back || recent.get(back).close().signum() == 0) {
            return null;
        }
        return recent.get(0).close().doubleValue() / recent.get(back).close().doubleValue() - 1;
    }
}
