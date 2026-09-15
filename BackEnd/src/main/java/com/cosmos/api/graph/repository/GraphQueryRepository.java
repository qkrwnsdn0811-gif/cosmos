package com.cosmos.api.graph.repository;

import java.math.BigDecimal;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
public class GraphQueryRepository {

    private final NamedParameterJdbcTemplate jdbcTemplate;

    public GraphQueryRepository(NamedParameterJdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public Optional<SnapshotRow> findLatestPublishedSnapshot() {
        return jdbcTemplate.query("""
                        SELECT snapshot_id, as_of_at
                        FROM graph_snapshot
                        WHERE status = 'PUBLISHED'
                        ORDER BY as_of_at DESC, snapshot_id DESC
                        LIMIT 1
                        """, Map.of(), (rs, rowNum) -> new SnapshotRow(
                        rs.getObject("snapshot_id", UUID.class),
                        rs.getTimestamp("as_of_at").toInstant()))
                .stream().findFirst();
    }

    public List<NodeRow> findActiveNodes(List<String> markets, UUID industryId) {
        String sql = """
                SELECT c.company_id, c.name, c.stock_code, c.market,
                       primary_industry.name AS industry_name
                FROM company c
                LEFT JOIN LATERAL (
                    SELECT i.name
                    FROM company_industry ci
                    JOIN industry i ON i.industry_id = ci.industry_id
                    WHERE ci.company_id = c.company_id
                    ORDER BY ci.is_primary DESC, i.name, i.industry_id
                    LIMIT 1
                ) primary_industry ON TRUE
                WHERE c.status = 'ACTIVE'
                  AND c.market IN (:markets)
                  AND (CAST(:industryId AS UUID) IS NULL OR EXISTS (
                      SELECT 1 FROM company_industry filter_ci
                      WHERE filter_ci.company_id = c.company_id
                        AND filter_ci.industry_id = CAST(:industryId AS UUID)
                  ))
                ORDER BY c.name, c.company_id
                """;
        MapSqlParameterSource params = new MapSqlParameterSource()
                .addValue("markets", markets)
                .addValue("industryId", industryId);
        return jdbcTemplate.query(sql, params, (rs, rowNum) -> new NodeRow(
                rs.getObject("company_id", UUID.class),
                rs.getString("name"),
                rs.getString("stock_code"),
                rs.getString("market"),
                rs.getString("industry_name")));
    }

    public List<NodeRow> findActiveNodesByIds(List<UUID> companyIds) {
        if (companyIds.isEmpty()) {
            return List.of();
        }
        return jdbcTemplate.query("""
                        SELECT c.company_id, c.name, c.stock_code, c.market,
                               primary_industry.name AS industry_name
                        FROM company c
                        LEFT JOIN LATERAL (
                            SELECT i.name
                            FROM company_industry ci
                            JOIN industry i ON i.industry_id = ci.industry_id
                            WHERE ci.company_id = c.company_id
                            ORDER BY ci.is_primary DESC, i.name, i.industry_id
                            LIMIT 1
                        ) primary_industry ON TRUE
                        WHERE c.status = 'ACTIVE' AND c.company_id IN (:companyIds)
                        """,
                Map.of("companyIds", companyIds),
                (rs, rowNum) -> new NodeRow(
                        rs.getObject("company_id", UUID.class),
                        rs.getString("name"),
                        rs.getString("stock_code"),
                        rs.getString("market"),
                        rs.getString("industry_name")));
    }

    public List<EdgeRow> findEdges(UUID snapshotId, String window) {
        return jdbcTemplate.query("""
                        SELECT cr.relationship_id, cr.source_company_id, cr.target_company_id,
                               rt.code AS relationship_type, rsc.score,
                               rsc.news_score, rsc.disclosure_score, rsc.impact_direction
                        FROM relationship_score_current rsc
                        JOIN company_relationship cr ON cr.relationship_id = rsc.relationship_id
                        JOIN relationship_type rt
                          ON rt.relationship_type_id = cr.relationship_type_id
                        JOIN company source_company
                          ON source_company.company_id = cr.source_company_id
                         AND source_company.status = 'ACTIVE'
                        JOIN company target_company
                          ON target_company.company_id = cr.target_company_id
                         AND target_company.status = 'ACTIVE'
                        WHERE rsc.snapshot_id = :snapshotId AND rsc.window_type = :window
                        ORDER BY rsc.score DESC, cr.relationship_id
                        """,
                Map.of("snapshotId", snapshotId, "window", window),
                (rs, rowNum) -> new EdgeRow(
                        rs.getObject("relationship_id", UUID.class),
                        rs.getObject("source_company_id", UUID.class),
                        rs.getObject("target_company_id", UUID.class),
                        rs.getString("relationship_type"),
                        rs.getBigDecimal("score"),
                        rs.getBigDecimal("news_score"),
                        rs.getBigDecimal("disclosure_score"),
                        rs.getString("impact_direction")));
    }

    public Optional<RelationshipRow> findRelationship(UUID relationshipId, String window) {
        return jdbcTemplate.query("""
                        SELECT cr.relationship_id,
                               source_company.company_id AS source_company_id,
                               source_company.name AS source_company_name,
                               target_company.company_id AS target_company_id,
                               target_company.name AS target_company_name,
                               rt.code AS relationship_type, rt.directionality,
                               rsc.window_type, rsc.score, rsc.news_score,
                               rsc.disclosure_score, rsc.impact_direction,
                               rsc.confidence, rsc.evidence_count, rsc.as_of_at
                        FROM company_relationship cr
                        JOIN company source_company ON source_company.company_id = cr.source_company_id
                        JOIN company target_company ON target_company.company_id = cr.target_company_id
                        JOIN relationship_type rt ON rt.relationship_type_id = cr.relationship_type_id
                        JOIN relationship_score_current rsc
                          ON rsc.relationship_id = cr.relationship_id
                         AND rsc.window_type = :window
                        JOIN graph_snapshot gs ON gs.snapshot_id = rsc.snapshot_id
                        WHERE cr.relationship_id = :relationshipId
                          AND source_company.status = 'ACTIVE'
                          AND target_company.status = 'ACTIVE'
                          AND gs.status = 'PUBLISHED'
                        ORDER BY gs.as_of_at DESC
                        LIMIT 1
                        """,
                Map.of("relationshipId", relationshipId, "window", window),
                (rs, rowNum) -> new RelationshipRow(
                        rs.getObject("relationship_id", UUID.class),
                        rs.getObject("source_company_id", UUID.class),
                        rs.getString("source_company_name"),
                        rs.getObject("target_company_id", UUID.class),
                        rs.getString("target_company_name"),
                        rs.getString("relationship_type"),
                        rs.getString("directionality"),
                        rs.getString("window_type"),
                        rs.getBigDecimal("score"),
                        rs.getBigDecimal("news_score"),
                        rs.getBigDecimal("disclosure_score"),
                        rs.getString("impact_direction"),
                        rs.getBigDecimal("confidence"),
                        rs.getInt("evidence_count"),
                        rs.getTimestamp("as_of_at").toInstant()))
                .stream().findFirst();
    }

    public boolean existsRelationship(UUID relationshipId) {
        Boolean exists = jdbcTemplate.queryForObject("""
                        SELECT EXISTS (
                            SELECT 1 FROM company_relationship
                            WHERE relationship_id = :relationshipId
                        )
                        """, Map.of("relationshipId", relationshipId), Boolean.class);
        return Boolean.TRUE.equals(exists);
    }

    public List<EvidenceRow> findNewsEvidence(
            UUID relationshipId,
            Instant cursorPublishedAt,
            UUID cursorNewsId,
            int limit
    ) {
        String sql = """
                WITH representative_evidence AS (
                    SELECT DISTINCT ON (sd.document_id)
                           sd.document_id AS news_id, sd.title, sd.summary,
                           na.publisher, sd.original_url, sd.published_at,
                           de.sentence_text AS evidence_sentence,
                           re.contribution_score, de.confidence
                    FROM relationship_evidence re
                    JOIN source_document sd ON sd.document_id = re.document_id
                    JOIN news_article na ON na.document_id = sd.document_id
                    LEFT JOIN document_evidence de ON de.evidence_id = re.evidence_id
                    WHERE re.relationship_id = :relationshipId
                      AND sd.document_type = 'NEWS'
                      AND sd.published_at IS NOT NULL
                    ORDER BY sd.document_id,
                             re.contribution_score DESC NULLS LAST,
                             re.relationship_evidence_id
                )
                SELECT news_id, title, summary, publisher, original_url, published_at,
                       evidence_sentence, contribution_score, confidence
                FROM representative_evidence
                WHERE CAST(:cursorPublishedAt AS TIMESTAMPTZ) IS NULL OR
                      (published_at, news_id) <
                      (CAST(:cursorPublishedAt AS TIMESTAMPTZ), CAST(:cursorNewsId AS UUID))
                ORDER BY published_at DESC, news_id DESC
                LIMIT :limit
                """;
        MapSqlParameterSource params = new MapSqlParameterSource()
                .addValue("relationshipId", relationshipId)
                .addValue("cursorPublishedAt", cursorPublishedAt == null ? null : Timestamp.from(cursorPublishedAt))
                .addValue("cursorNewsId", cursorNewsId)
                .addValue("limit", limit);
        return jdbcTemplate.query(sql, params, (rs, rowNum) -> new EvidenceRow(
                rs.getObject("news_id", UUID.class),
                rs.getString("title"),
                rs.getString("summary"),
                rs.getString("publisher"),
                rs.getString("original_url"),
                rs.getTimestamp("published_at").toInstant(),
                rs.getString("evidence_sentence"),
                rs.getBigDecimal("contribution_score"),
                rs.getBigDecimal("confidence")));
    }

    public record SnapshotRow(UUID snapshotId, Instant asOfAt) {
    }

    public record NodeRow(UUID companyId, String name, String stockCode, String market, String industryName) {
    }

    public record EdgeRow(
            UUID relationshipId,
            UUID sourceCompanyId,
            UUID targetCompanyId,
            String relationshipType,
            BigDecimal score,
            BigDecimal newsScore,
            BigDecimal disclosureScore,
            String impactDirection
    ) {
    }

    public record RelationshipRow(
            UUID relationshipId,
            UUID sourceCompanyId,
            String sourceCompanyName,
            UUID targetCompanyId,
            String targetCompanyName,
            String relationshipType,
            String directionality,
            String window,
            BigDecimal score,
            BigDecimal newsScore,
            BigDecimal disclosureScore,
            String impactDirection,
            BigDecimal confidence,
            int evidenceCount,
            Instant asOfAt
    ) {
    }

    public record EvidenceRow(
            UUID newsId,
            String title,
            String summary,
            String publisher,
            String originalUrl,
            Instant publishedAt,
            String evidenceSentence,
            BigDecimal contributionScore,
            BigDecimal confidence
    ) {
    }
}
