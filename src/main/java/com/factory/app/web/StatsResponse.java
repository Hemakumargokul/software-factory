package com.factory.app.web;

/** Response DTO for {@code GET /api/stats/{code}}: {"code", "url", "clicks"}. */
public record StatsResponse(String code, String url, long clicks) {
}
