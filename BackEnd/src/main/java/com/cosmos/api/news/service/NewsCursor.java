package com.cosmos.api.news.service;

import java.time.Instant;
import java.util.UUID;

record NewsCursor(Instant publishedAt, UUID newsId) {
}
