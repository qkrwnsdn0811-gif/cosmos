package com.cosmos.api.company.entity;

import jakarta.persistence.Column;
import jakarta.persistence.EmbeddedId;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.MapsId;
import jakarta.persistence.Table;
import java.math.BigDecimal;
import java.time.Instant;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Entity
@Table(name = "company_metric_history")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class CompanyMetricHistory {

    @EmbeddedId
    private CompanyMetricHistoryId id;

    @MapsId("companyId")
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "company_id", nullable = false)
    private Company company;

    @Column(name = "measured_at", nullable = false, insertable = false, updatable = false)
    private Instant measuredAt;

    @Column(name = "window_type", nullable = false, insertable = false, updatable = false, length = 30)
    private String windowType;

    @Column(name = "news_mention_count", nullable = false)
    private int newsMentionCount;

    @Column(name = "positive_count", nullable = false)
    private int positiveCount;

    @Column(name = "negative_count", nullable = false)
    private int negativeCount;

    @Column(name = "sentiment_score", precision = 18, scale = 6)
    private BigDecimal sentimentScore;

    @Column(name = "relationship_count", nullable = false)
    private int relationshipCount;

    @Column(name = "metric_version", nullable = false, length = 50)
    private String metricVersion;
}
