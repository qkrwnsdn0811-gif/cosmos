package com.cosmos.api.graph.dto;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public record LatestGraphResponse(
        UUID snapshotId,
        Instant asOfAt,
        Instant nextRefreshAt,
        boolean personalized,
        List<GraphNodeResponse> nodes,
        List<GraphEdgeResponse> edges
) {
    public LatestGraphResponse {
        nodes = List.copyOf(nodes);
        edges = List.copyOf(edges);
    }
}
