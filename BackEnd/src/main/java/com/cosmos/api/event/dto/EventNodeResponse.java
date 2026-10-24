package com.cosmos.api.event.dto;

import com.cosmos.api.company.dto.CompanyFundamentalResponse;
import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;

/**
 * 지도 노드 하나. basis는 REPORTED(기사 언급, 근거) | INFERRED(공급망 전이, 추정), status는 OK | HOLD.
 * anchorCompanyId는 추정 노드가 퍼져 나온 언급 기업이며 REPORTED면 null이다.
 * impactDirection·impactMagnitude·confidence는 영향 모델(nexus predict-impact)이 채운다.
 * confidence가 있는데 impactDirection이 null이면 신뢰도가 표시 기준 미만인 방향 보류다.
 * position·signals는 과거 사례 통계로 붙인 위치와 방향 딱지(nexus cases)이며 딱지가 없으면 null·빈 목록이다.
 * fundamental은 기업 지표이고 아직 없으면 null이다.
 */
public record EventNodeResponse(
        UUID companyId,
        String name,
        String stockCode,
        String market,
        String basis,
        int hop,
        BigDecimal score,
        String status,
        String holdReason,
        String impactDirection,
        BigDecimal impactMagnitude,
        BigDecimal confidence,
        UUID anchorCompanyId,
        List<PathStepResponse> path,
        List<AbnormalReturnResponse> abnormalReturns,
        String position,
        String positionLabel,
        List<CompanySignalResponse> signals,
        CompanyFundamentalResponse fundamental
) {
    public EventNodeResponse {
        path = List.copyOf(path);
        abnormalReturns = List.copyOf(abnormalReturns);
        signals = List.copyOf(signals);
    }
}
