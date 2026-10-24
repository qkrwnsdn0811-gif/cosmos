package com.cosmos.api.event.type;

import java.util.Arrays;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 뉴스 유형(F2). news_event.event_type 코드와 화면 이름. 파이프라인(nexus/nexus/cases/newstype.py)의 LABELS와 같아야 한다.
 * 유형은 과거 사례를 비교 가능하게 묶는 기준이다("과거 수출 규제 뉴스 뒤 공급사는 어떻게 움직였나").
 */
public enum NewsType {

    ACCIDENT("사고·공급 차질"),
    EXPORT_CONTROL("수출 규제·제재"),
    TARIFF("관세·통상"),
    EARNINGS_BEAT("실적 호조"),
    EARNINGS_MISS("실적 부진"),
    SUPPLY_DEAL("수주·공급 계약"),
    PRICE_UP("제품 가격 상승"),
    PRICE_DOWN("제품 가격 하락"),
    PRODUCTION_CUT("감산·재고 조정"),
    CAPEX("증설·투자"),
    SHAREHOLDER("주주환원"),
    AI_DEMAND("AI·HBM 수요"),
    POLICY("정부 정책·지원"),
    ANALYST("증권가 전망·목표가"),
    MARKET_WRAP("증시 시황"),
    OTHER("기타");

    private static final Map<String, NewsType> BY_CODE = Arrays.stream(values())
            .collect(Collectors.toUnmodifiableMap(NewsType::name, Function.identity()));

    private final String label;

    NewsType(String label) {
        this.label = label;
    }

    public String label() {
        return label;
    }

    public static boolean isKnown(String code) {
        return code != null && BY_CODE.containsKey(code);
    }

    /** 코드가 없거나 모르는 값이면 null. 화면은 코드 대신 이 이름을 보여준다. */
    public static String labelOf(String code) {
        NewsType type = code == null ? null : BY_CODE.get(code);
        return type == null ? null : type.label;
    }
}
