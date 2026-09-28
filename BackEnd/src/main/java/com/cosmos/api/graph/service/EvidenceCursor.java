package com.cosmos.api.graph.service;

import java.time.Instant;
import java.util.UUID;

record EvidenceCursor(Instant publishedAt, UUID newsId) {
}
