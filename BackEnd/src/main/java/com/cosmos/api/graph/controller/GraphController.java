package com.cosmos.api.graph.controller;

import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.graph.dto.CompanyGraphResponse;
import com.cosmos.api.graph.dto.LatestGraphResponse;
import com.cosmos.api.graph.dto.RelationshipDetailResponse;
import com.cosmos.api.graph.dto.RelationshipEvidenceResponse;
import com.cosmos.api.graph.service.GraphService;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import java.util.UUID;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@Validated
@RestController
@RequestMapping("/api")
public class GraphController {

    private final GraphService graphService;

    public GraphController(GraphService graphService) {
        this.graphService = graphService;
    }

    @GetMapping("/graphs/latest")
    public LatestGraphResponse findLatestGraph(
            @RequestParam(required = false) @Size(max = 100) String universe,
            @RequestParam(required = false) UUID industryId
    ) {
        return graphService.findLatestGraph(universe, industryId);
    }

    @GetMapping("/graphs/companies/{companyId}")
    public CompanyGraphResponse findCompanyGraph(
            @PathVariable UUID companyId,
            @RequestParam(defaultValue = "3") @Min(1) @Max(3) int maxDepth
    ) {
        return graphService.findCompanyGraph(companyId, maxDepth);
    }

    @GetMapping("/relationships/{relationshipId}")
    public RelationshipDetailResponse findRelationship(
            @PathVariable UUID relationshipId,
            @RequestParam(defaultValue = "30D") String window
    ) {
        return graphService.findRelationship(relationshipId, window);
    }

    @GetMapping("/relationships/{relationshipId}/evidence")
    public CursorPageResponse<RelationshipEvidenceResponse> findRelationshipEvidence(
            @PathVariable UUID relationshipId,
            @RequestParam(required = false) String cursor,
            @RequestParam(defaultValue = "10") @Min(1) @Max(20) int size
    ) {
        return graphService.findRelationshipEvidence(relationshipId, cursor, size);
    }
}
