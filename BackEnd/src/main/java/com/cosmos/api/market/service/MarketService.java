package com.cosmos.api.market.service;

import com.cosmos.api.company.type.StockPeriod;
import com.cosmos.api.market.dto.IndexPointResponse;
import com.cosmos.api.market.dto.MarketIndexListResponse;
import com.cosmos.api.market.dto.MarketIndexResponse;
import com.cosmos.api.market.repository.MarketQueryRepository;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** 기업 탭 시장 띠·상세 차트의 지수. 기간은 거래일 수로 센다(1M·3M·6M·1Y = 20·60·120·250). */
@Service
@Transactional(readOnly = true)
public class MarketService {

    static final List<String> INDEX_CODES = List.of("KOSPI", "KOSDAQ");
    static final int MONTH_DAYS = 20;

    private final MarketQueryRepository marketRepository;

    public MarketService(MarketQueryRepository marketRepository) {
        this.marketRepository = marketRepository;
    }

    public MarketIndexListResponse findIndices(String period) {
        int days = tradingDays(StockPeriod.from(period));
        List<MarketIndexResponse> items = new ArrayList<>();
        for (String code : INDEX_CODES) {
            // 최근 것부터. 1개월 등락에는 21개가 필요하다
            List<IndexPointResponse> recent = marketRepository.findRecentCloses(code, Math.max(days + 1, MONTH_DAYS + 1));
            if (recent.isEmpty()) {
                continue;
            }
            List<IndexPointResponse> points = new ArrayList<>(recent.subList(0, Math.min(days + 1, recent.size())));
            points.sort(Comparator.comparing(IndexPointResponse::date));
            items.add(new MarketIndexResponse(code, recent.get(0).close(), change(recent, 1), change(recent, MONTH_DAYS),
                    points));
        }
        LocalDate asOf = items.stream().map(i -> i.points().get(i.points().size() - 1).date())
                .filter(Objects::nonNull).max(Comparator.naturalOrder()).orElse(null);
        return new MarketIndexListResponse(asOf, items);
    }

    static int tradingDays(StockPeriod period) {
        return switch (period) {
            case MONTH_1 -> 20;
            case MONTH_3 -> 60;
            case MONTH_6 -> 120;
            case YEAR_1 -> 250;
        };
    }

    /** recent 는 최근 것부터. back 거래일 전 종가 대비 비율, 모자라면 null */
    private static Double change(List<IndexPointResponse> recent, int back) {
        if (recent.size() <= back || recent.get(back).close().signum() == 0) {
            return null;
        }
        return recent.get(0).close().doubleValue() / recent.get(back).close().doubleValue() - 1;
    }
}
