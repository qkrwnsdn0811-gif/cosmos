package com.cosmos.api.company.dto;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

/**
 * 기업 탭 시세판의 한 종목. 등락(change*)은 비율(0.0327 = 3.27%)이고 거래일 수로 센다: 1일 = 1거래일, 1개월 = 20거래일,
 * 3개월 = 60거래일 전 종가 대비. 거래일이 모자라면 null. spark 는 최근 21개 종가(오래된 것부터, 모자라면 있는 만큼).
 * 주가가 없으면 시세 칸이 null(spark 는 빈 목록), 지표가 없으면 지표 칸이 null. relationCount 는 공개 스냅샷 1년 관계의 상대 수.
 */
public record CompanyQuoteResponse(
        UUID companyId,
        String name,
        String stockCode,
        String market,
        String industry,
        BigDecimal close,
        BigDecimal prevClose,
        LocalDate tradingDate,
        Double change1d,
        Double change1m,
        Double change3m,
        List<BigDecimal> spark,
        BigDecimal marketCap,
        Integer marketCapRank,
        String sizeClass,
        BigDecimal per,
        BigDecimal pbr,
        BigDecimal dividendYield,
        BigDecimal dividendPerShare,
        BigDecimal netIncomeTtm,
        BigDecimal equity,
        String fiscalBasis,
        LocalDate priceDate,
        int relationCount
) {
}
