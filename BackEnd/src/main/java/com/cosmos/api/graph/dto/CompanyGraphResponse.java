package com.cosmos.api.graph.dto;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public record CompanyGraphResponse(
        UUID snapshotId,
        Instant asOfAt,
        UUID centerCompanyId,
        boolean personalized,
        List<CompanyGraphNodeResponse> nodes,
        List<GraphEdgeResponse> edges
) {
    public CompanyGraphResponse {
        nodes = List.copyOf(nodes);
        edges = List.copyOf(edges);
    }
}
