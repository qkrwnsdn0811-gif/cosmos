package com.cosmos.api.event.type;

import java.util.Arrays;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 이벤트에서 기업의 위치. REPORTED는 기사에 나온 기업이고, 나머지는 추정 경로 마지막 단계의 관계다
 * (SUPPLY 관계면 공급사·고객사, 그 밖에는 경쟁사·협력사·지분 관계, 동시언급만이면 함께 언급).
 * 파이프라인(nexus/nexus/cases/reaction.py)의 POSITIONS와 같아야 한다.
 */
public enum CompanyPosition {

    REPORTED("기사에 나온 기업"),
    SUPPLIER("공급사"),
    CUSTOMER("고객사"),
    COMPETITOR("경쟁사"),
    PARTNER("협력사"),
    INVEST("지분 관계"),
    COMENTION("함께 언급");

    private static final Map<String, CompanyPosition> BY_CODE = Arrays.stream(values())
            .collect(Collectors.toUnmodifiableMap(CompanyPosition::name, Function.identity()));

    private final String label;

    CompanyPosition(String label) {
        this.label = label;
    }

    public String label() {
        return label;
    }

    /** 코드가 없거나 모르는 값이면 null. */
    public static String labelOf(String code) {
        CompanyPosition position = code == null ? null : BY_CODE.get(code);
        return position == null ? null : position.label;
    }
}
