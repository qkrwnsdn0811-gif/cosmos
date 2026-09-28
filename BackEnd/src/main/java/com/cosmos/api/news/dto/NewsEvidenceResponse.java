package com.cosmos.api.news.dto;

import java.math.BigDecimal;

public record NewsEvidenceResponse(String sentence, BigDecimal confidence) {
}
