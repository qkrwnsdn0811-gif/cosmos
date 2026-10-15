package com.cosmos.api.event.service;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** event_company_impact.path(JSONB) → 단계 목록. 형식: [{"src","dst","rel","source","strength"}], 기업은 "MARKET:TICKER". */
@Component
public class EventPathParser {

    private final ObjectMapper objectMapper;

    public EventPathParser(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    public List<RawStep> parse(String pathJson) {
        if (pathJson == null || pathJson.isBlank()) {
            return List.of();
        }
        JsonNode root = objectMapper.readTree(pathJson);
        List<RawStep> steps = new ArrayList<>();
        for (JsonNode step : root) {
            JsonNode strength = step.path("strength");
            steps.add(new RawStep(
                    text(step, "src"), text(step, "dst"), text(step, "rel"), text(step, "source"),
                    strength.isNumber() ? strength.decimalValue() : null));
        }
        return steps;
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.path(field);
        return value.isString() ? value.asString() : null;
    }

    public record RawStep(String src, String dst, String rel, String source, BigDecimal strength) {
    }
}
