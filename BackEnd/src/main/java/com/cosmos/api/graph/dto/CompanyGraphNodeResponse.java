package com.cosmos.api.graph.dto;

import java.util.UUID;

public record CompanyGraphNodeResponse(UUID companyId, String name, int depth) {
}
