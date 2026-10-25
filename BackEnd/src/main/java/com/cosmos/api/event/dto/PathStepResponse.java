package com.cosmos.api.event.dto;

import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;

/**
 * 추정 경로의 한 단계. source/target은 실제 관계 방향이다(전파 방향과 다를 수 있다).
 * 경로의 기업이 마스터에 없으면 id와 이름은 null이다. edgeSource는 COSMOS | NEWS | COMENTION.
 */
public record PathStepResponse(
        UUID sourceCompanyId,
        String sourceName,
        UUID targetCompanyId,
        String targetName,
        String relationshipType,
        String edgeSource,
        BigDecimal strength,
        List<EvidenceSnippetResponse> evidence
) {
    public PathStepResponse {
        evidence = List.copyOf(evidence);
    }
}
