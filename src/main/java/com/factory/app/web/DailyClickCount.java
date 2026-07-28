package com.factory.app.web;

/**
 * Response element DTO for {@code GET /api/stats/{code}/daily}: one entry per
 * UTC calendar day that had at least one recorded click, with exactly two
 * fields: {@code date} (format {@code yyyy-MM-dd}) and {@code clicks}
 * (non-negative count of clicks recorded on that day).
 */
public record DailyClickCount(String date, long clicks) {
}
