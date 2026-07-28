package com.factory.app.web;

/** Response DTO for {@code POST /api/shorten}: {"code": ..., "url": ...}. */
public record ShortenResponse(String code, String url) {
}
