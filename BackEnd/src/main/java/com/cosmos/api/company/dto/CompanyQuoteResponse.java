package com.cosmos.api.company.dto;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

/**
 * 기업 탭 시세판의 한 종목. 등락(change*)은 비율(0.0327 = 3.27%)이고 거래일 수로 센다: 1일 = 1거래일, 1개월 = 20거래일,
 * 3개월 = 60거래일 전 종가 대비. 거래일이 모자라면 null. spark 는 최근 21개 종가(오래된 것부터, 모자라면 있는 만큼).
 * 주가가 없으면 시세 칸이 null(spark 는 빈 목록), 지표가 없으면 지표 칸이 null. relationCount 는 공개 스냅샷 1년 관계의 상대 수.
 * news7d 는 마지막 뉴스 날(KST) 기준 최근 7일 이 기업이 직접 나온 기사 수, newsUsual7d 는 그 전 60일의 7일 평균.
 * high52w·low52w 는 최근 250거래일 고가 최고·저가 최저(주가 없으면 null). 시세판의 '주목 종목' 표시에 쓴다.
 * expectedMove*·moveGrade* 는 반도체 기준 예상 변동폭(|초과수익률| 평균, 비율)과 등급(SMALL·MID·LARGE), Short = 3거래일 · Long = 20거래일.
 * nexus cases 가 쓴 값이며 없으면 null. 투자 성향 맞춤 목록이 쓴다(등급은 검증을 통과한 기간에만 쓴다).
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
        int relationCount,
        int news7d,
        Double newsUsual7d,
        BigDecimal high52w,
        BigDecimal low52w,
        Double expectedMoveShort,
        String moveGradeShort,
        Double expectedMoveLong,
        String moveGradeLong
) {
}
