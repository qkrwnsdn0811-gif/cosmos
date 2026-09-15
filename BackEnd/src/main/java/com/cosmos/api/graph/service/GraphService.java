package com.cosmos.api.graph.service;

import com.cosmos.api.company.error.CompanyErrorCode;
import com.cosmos.api.company.repository.CompanyRepository;
import com.cosmos.api.company.type.MetricWindow;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.graph.dto.CompanyGraphResponse;
import com.cosmos.api.graph.dto.CompanyGraphNodeResponse;
import com.cosmos.api.graph.dto.GraphEdgeResponse;
import com.cosmos.api.graph.dto.GraphNodeResponse;
import com.cosmos.api.graph.dto.LatestGraphResponse;
import com.cosmos.api.graph.dto.RelationshipCompanyResponse;
import com.cosmos.api.graph.dto.RelationshipDetailResponse;
import com.cosmos.api.graph.dto.RelationshipEvidenceResponse;
import com.cosmos.api.graph.error.GraphErrorCode;
import com.cosmos.api.graph.repository.GraphQueryRepository;
import com.cosmos.api.graph.repository.GraphQueryRepository.EdgeRow;
import com.cosmos.api.graph.repository.GraphQueryRepository.EvidenceRow;
import com.cosmos.api.graph.repository.GraphQueryRepository.NodeRow;
import com.cosmos.api.graph.repository.GraphQueryRepository.RelationshipRow;
import com.cosmos.api.graph.repository.GraphQueryRepository.SnapshotRow;
import java.time.temporal.ChronoUnit;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
public class GraphService {

    private static final String GRAPH_WINDOW = "30D";
    private static final Set<String> SUPPORTED_UNIVERSES = Set.of("KOSPI100", "NASDAQ100");

    private final GraphQueryRepository graphRepository;
    private final CompanyRepository companyRepository;
    private final EvidenceCursorCodec cursorCodec;

    public GraphService(
            GraphQueryRepository graphRepository,
            CompanyRepository companyRepository,
            EvidenceCursorCodec cursorCodec
    ) {
        this.graphRepository = graphRepository;
        this.companyRepository = companyRepository;
        this.cursorCodec = cursorCodec;
    }

    public LatestGraphResponse findLatestGraph(String universeValue, UUID industryId) {
        List<String> markets = parseUniverses(universeValue);
        SnapshotRow snapshot = latestSnapshot();
        List<NodeRow> nodeRows = graphRepository.findActiveNodes(markets, industryId);
        Set<UUID> nodeIds = nodeRows.stream().map(NodeRow::companyId).collect(java.util.stream.Collectors.toSet());
        List<EdgeRow> edges = graphRepository.findEdges(snapshot.snapshotId(), GRAPH_WINDOW).stream()
                .filter(edge -> nodeIds.contains(edge.sourceCompanyId())
                        && nodeIds.contains(edge.targetCompanyId()))
                .toList();

        return new LatestGraphResponse(
                snapshot.snapshotId(),
                snapshot.asOfAt(),
                snapshot.asOfAt().plus(1, ChronoUnit.HOURS),
                false,
                nodeRows.stream().map(this::toNode).toList(),
                edges.stream().map(this::toEdge).toList());
    }

    public CompanyGraphResponse findCompanyGraph(
            UUID companyId,
            int maxDepth
    ) {
        if (maxDepth < 1 || maxDepth > 3) {
            throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
        }
        if (companyRepository.findActiveById(companyId).isEmpty()) {
            throw new BusinessException(CompanyErrorCode.COMPANY_NOT_FOUND);
        }
        SnapshotRow snapshot = latestSnapshot();
        List<EdgeRow> allEdges = graphRepository.findEdges(snapshot.snapshotId(), GRAPH_WINDOW);
        Map<UUID, List<EdgeRow>> adjacency = adjacency(allEdges);
        LinkedHashMap<UUID, Integer> depths = traverse(companyId, adjacency, maxDepth);
        List<NodeRow> nodeRows = graphRepository.findActiveNodesByIds(new ArrayList<>(depths.keySet()));
        Map<UUID, NodeRow> nodesById = new HashMap<>();
        nodeRows.forEach(node -> nodesById.put(node.companyId(), node));
        List<CompanyGraphNodeResponse> nodes = depths.entrySet().stream()
                .filter(entry -> nodesById.containsKey(entry.getKey()))
                .map(entry -> new CompanyGraphNodeResponse(
                        entry.getKey(), nodesById.get(entry.getKey()).name(), entry.getValue()))
                .toList();
        Set<UUID> selectedIds = depths.keySet();
        List<GraphEdgeResponse> edges = allEdges.stream()
                .filter(edge -> selectedIds.contains(edge.sourceCompanyId())
                        && selectedIds.contains(edge.targetCompanyId()))
                .map(this::toEdge)
                .toList();

        return new CompanyGraphResponse(
                snapshot.snapshotId(), snapshot.asOfAt(), companyId, false, nodes, edges);
    }

    public RelationshipDetailResponse findRelationship(UUID relationshipId, String windowValue) {
        MetricWindow window = MetricWindow.from(windowValue);
        RelationshipRow row = graphRepository.findRelationship(relationshipId, window.code())
                .orElseThrow(() -> new BusinessException(GraphErrorCode.RELATIONSHIP_NOT_FOUND));
        return new RelationshipDetailResponse(
                row.relationshipId(),
                new RelationshipCompanyResponse(row.sourceCompanyId(), row.sourceCompanyName()),
                new RelationshipCompanyResponse(row.targetCompanyId(), row.targetCompanyName()),
                row.relationshipType(),
                row.directionality(),
                row.window(),
                row.score(),
                row.newsScore(),
                row.disclosureScore(),
                row.impactDirection(),
                row.confidence(),
                row.evidenceCount(),
                row.asOfAt(),
                false);
    }

    public CursorPageResponse<RelationshipEvidenceResponse> findRelationshipEvidence(
            UUID relationshipId,
            String encodedCursor,
            int size
    ) {
        if (size < 1 || size > 20) {
            throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
        }
        if (!graphRepository.existsRelationship(relationshipId)) {
            throw new BusinessException(GraphErrorCode.RELATIONSHIP_NOT_FOUND);
        }
        EvidenceCursor cursor = cursorCodec.decode(encodedCursor);
        List<EvidenceRow> rows = graphRepository.findNewsEvidence(
                relationshipId,
                cursor == null ? null : cursor.publishedAt(),
                cursor == null ? null : cursor.newsId(),
                size + 1);
        boolean hasNext = rows.size() > size;
        List<EvidenceRow> pageRows = hasNext ? rows.subList(0, size) : rows;
        String nextCursor = null;
        if (hasNext && !pageRows.isEmpty()) {
            EvidenceRow last = pageRows.getLast();
            nextCursor = cursorCodec.encode(new EvidenceCursor(last.publishedAt(), last.newsId()));
        }
        List<RelationshipEvidenceResponse> items = pageRows.stream()
                .map(row -> new RelationshipEvidenceResponse(
                        row.newsId(), row.title(), row.summary(), row.publisher(), row.originalUrl(),
                        row.publishedAt(), row.evidenceSentence(), row.contributionScore(), row.confidence()))
                .toList();
        return CursorPageResponse.of(items, nextCursor, hasNext);
    }

    private SnapshotRow latestSnapshot() {
        return graphRepository.findLatestPublishedSnapshot()
                .orElseThrow(() -> new BusinessException(GraphErrorCode.GRAPH_SNAPSHOT_NOT_FOUND));
    }

    private List<String> parseUniverses(String universeValue) {
        String resolved = universeValue == null || universeValue.isBlank()
                ? "KOSPI100,NASDAQ100"
                : universeValue;
        LinkedHashSet<String> universes = new LinkedHashSet<>();
        for (String item : resolved.split(",")) {
            String universe = item.trim().toUpperCase();
            if (!SUPPORTED_UNIVERSES.contains(universe)) {
                throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
            }
            universes.add(universe);
        }
        if (universes.isEmpty()) {
            throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
        }
        return universes.stream()
                .map(universe -> universe.equals("KOSPI100") ? "KOSPI" : "NASDAQ")
                .toList();
    }

    private Map<UUID, List<EdgeRow>> adjacency(List<EdgeRow> edges) {
        Map<UUID, List<EdgeRow>> result = new HashMap<>();
        for (EdgeRow edge : edges) {
            result.computeIfAbsent(edge.sourceCompanyId(), ignored -> new ArrayList<>()).add(edge);
            result.computeIfAbsent(edge.targetCompanyId(), ignored -> new ArrayList<>()).add(edge);
        }
        Comparator<EdgeRow> order = Comparator.comparing(EdgeRow::score).reversed()
                .thenComparing(EdgeRow::relationshipId);
        result.values().forEach(items -> items.sort(order));
        return result;
    }

    private LinkedHashMap<UUID, Integer> traverse(
            UUID center,
            Map<UUID, List<EdgeRow>> adjacency,
            int maxDepth
    ) {
        LinkedHashMap<UUID, Integer> depths = new LinkedHashMap<>();
        ArrayDeque<UUID> queue = new ArrayDeque<>();
        depths.put(center, 0);
        queue.add(center);
        while (!queue.isEmpty()) {
            UUID current = queue.removeFirst();
            int depth = depths.get(current);
            if (depth >= maxDepth) {
                continue;
            }
            for (EdgeRow edge : adjacency.getOrDefault(current, List.of())) {
                UUID neighbor = edge.sourceCompanyId().equals(current)
                        ? edge.targetCompanyId() : edge.sourceCompanyId();
                if (depths.containsKey(neighbor)) {
                    continue;
                }
                depths.put(neighbor, depth + 1);
                queue.addLast(neighbor);
            }
        }
        return depths;
    }

    private GraphNodeResponse toNode(NodeRow row) {
        return new GraphNodeResponse(
                row.companyId(), row.name(), row.stockCode(), row.market(), row.industryName());
    }

    private GraphEdgeResponse toEdge(EdgeRow row) {
        return new GraphEdgeResponse(
                row.relationshipId(), row.sourceCompanyId(), row.targetCompanyId(),
                row.relationshipType(), row.score(), row.newsScore(), row.disclosureScore(),
                row.impactDirection());
    }
}
