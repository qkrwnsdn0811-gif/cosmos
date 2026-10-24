package com.cosmos.api.company.dto;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * 기업 지표(F8): 시가총액·PER·PBR·배당수익률·규모. 파이프라인(nexus fundamentals)이 OpenDART 재무제표와
 * 시가총액으로 계산해 company_fundamental에 넣는다. sizeClass는 LARGE | MID | SMALL.
 * 적자 기업의 PER처럼 계산할 수 없는 값은 null이고, 아직 지표가 없는 기업은 모든 값이 null이다.
 */
public record CompanyFundamentalResponse(
        BigDecimal marketCap,
        Integer marketCapRank,
        String sizeClass,
        BigDecimal per,
        BigDecimal pbr,
        BigDecimal dividendYield,
        BigDecimal netIncomeTtm,
        BigDecimal equity,
        BigDecimal dividendPerShare,
        BigDecimal closePrice,
        LocalDate priceDate,
        String fiscalBasis
) {

    public static CompanyFundamentalResponse empty() {
        return new CompanyFundamentalResponse(null, null, null, null, null, null, null, null, null, null, null, null);
    }
}
