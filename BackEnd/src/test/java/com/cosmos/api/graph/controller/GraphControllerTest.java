package com.cosmos.api.graph.controller;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.global.error.GlobalExceptionHandler;
import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.graph.dto.CompanyGraphNodeResponse;
import com.cosmos.api.graph.dto.CompanyGraphResponse;
import com.cosmos.api.graph.dto.GraphEdgeResponse;
import com.cosmos.api.graph.dto.GraphNodeResponse;
import com.cosmos.api.graph.dto.LatestGraphResponse;
import com.cosmos.api.graph.dto.RelationshipCompanyResponse;
import com.cosmos.api.graph.dto.RelationshipDetailResponse;
import com.cosmos.api.graph.dto.RelationshipEvidenceResponse;
import com.cosmos.api.graph.service.GraphService;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class GraphControllerTest {

    private static final UUID COMPANY_A = UUID.fromString("60000000-0000-0000-0000-000000000001");
    private static final UUID COMPANY_B = UUID.fromString("60000000-0000-0000-0000-000000000002");
    private static final UUID RELATIONSHIP_ID = UUID.fromString("60000000-0000-0000-0000-000000000010");
    private static final UUID SNAPSHOT_ID = UUID.fromString("60000000-0000-0000-0000-000000000020");
    private static final UUID NEWS_ID = UUID.fromString("60000000-0000-0000-0000-000000000030");
    private static final Instant AS_OF_AT = Instant.parse("2026-09-07T06:00:00Z");

    private GraphService graphService;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        graphService = mock(GraphService.class);
        mockMvc = MockMvcBuilders.standaloneSetup(new GraphController(graphService))
                .setControllerAdvice(new GlobalExceptionHandler())
                .build();
    }

    @Test
    void returnsLatestGraphUsingDocumentedDefaults() throws Exception {
        when(graphService.findLatestGraph(null, null)).thenReturn(new LatestGraphResponse(
                SNAPSHOT_ID,
                AS_OF_AT,
                AS_OF_AT.plusSeconds(3600),
                false,
                List.of(new GraphNodeResponse(COMPANY_A, "기업A", "A001", "KOSPI", "반도체")),
                List.of(edge())));

        mockMvc.perform(get("/api/graphs/latest"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.snapshotId").value(SNAPSHOT_ID.toString()))
                .andExpect(jsonPath("$.nextRefreshAt").value("2026-09-07T07:00:00Z"))
                .andExpect(jsonPath("$.personalized").value(false))
                .andExpect(jsonPath("$.nodes[0].industryName").value("반도체"))
                .andExpect(jsonPath("$.edges[0].relationshipType").value("SUPPLY"))
                .andExpect(jsonPath("$.edges[0].newsScore").value(75.0))
                .andExpect(jsonPath("$.edges[0].disclosureScore").value(90.0));

        verify(graphService).findLatestGraph(null, null);
    }

    @Test
    void returnsCompanyGraphUsingDocumentedDefaults() throws Exception {
        when(graphService.findCompanyGraph(COMPANY_A, 3))
                .thenReturn(new CompanyGraphResponse(
                        SNAPSHOT_ID,
                        AS_OF_AT,
                        COMPANY_A,
                        false,
                        List.of(new CompanyGraphNodeResponse(COMPANY_A, "기업A", 0)),
                        List.of(edge())));

        mockMvc.perform(get("/api/graphs/companies/{companyId}", COMPANY_A))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.centerCompanyId").value(COMPANY_A.toString()))
                .andExpect(jsonPath("$.nodes[0].depth").value(0))
                .andExpect(jsonPath("$.edges[0].newsScore").value(75.0))
                .andExpect(jsonPath("$.edges[0].disclosureScore").value(90.0));

        verify(graphService).findCompanyGraph(COMPANY_A, 3);
    }

    @Test
    void returnsRelationshipDetail() throws Exception {
        when(graphService.findRelationship(RELATIONSHIP_ID, "30D"))
                .thenReturn(new RelationshipDetailResponse(
                        RELATIONSHIP_ID,
                        new RelationshipCompanyResponse(COMPANY_A, "기업A"),
                        new RelationshipCompanyResponse(COMPANY_B, "기업B"),
                        "SUPPLY",
                        "DIRECTED",
                        "30D",
                        new BigDecimal("82.4"),
                        new BigDecimal("75.0"),
                        new BigDecimal("90.0"),
                        "POSITIVE",
                        new BigDecimal("0.88"),
                        16,
                        AS_OF_AT,
                        false));

        mockMvc.perform(get("/api/relationships/{relationshipId}", RELATIONSHIP_ID))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sourceCompany.companyId").value(COMPANY_A.toString()))
                .andExpect(jsonPath("$.targetCompany.companyId").value(COMPANY_B.toString()))
                .andExpect(jsonPath("$.window").value("30D"))
                .andExpect(jsonPath("$.newsScore").value(75.0))
                .andExpect(jsonPath("$.disclosureScore").value(90.0))
                .andExpect(jsonPath("$.evidenceCount").value(16));
    }

    @Test
    void returnsEvidenceCursorPage() throws Exception {
        when(graphService.findRelationshipEvidence(RELATIONSHIP_ID, null, 10))
                .thenReturn(CursorPageResponse.of(List.of(new RelationshipEvidenceResponse(
                        NEWS_ID,
                        "뉴스 제목",
                        "기사 요약",
                        "예시경제",
                        "https://example.com/news",
                        Instant.parse("2026-09-07T02:00:00Z"),
                        "관계 근거 문장",
                        new BigDecimal("0.76"),
                        new BigDecimal("0.91"))), null, false));

        mockMvc.perform(get("/api/relationships/{relationshipId}/evidence", RELATIONSHIP_ID))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].newsId").value(NEWS_ID.toString()))
                .andExpect(jsonPath("$.items[0].evidenceSentence").value("관계 근거 문장"))
                .andExpect(jsonPath("$.hasNext").value(false));
    }

    private GraphEdgeResponse edge() {
        return new GraphEdgeResponse(
                RELATIONSHIP_ID,
                COMPANY_A,
                COMPANY_B,
                "SUPPLY",
                new BigDecimal("82.4"),
                new BigDecimal("75.0"),
                new BigDecimal("90.0"),
                "POSITIVE");
    }
}
