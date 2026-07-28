package com.factory.app.web;

import com.factory.app.service.UrlShortenerService;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

/**
 * Handles {@code GET /api/stats/{code}} (read-only click-count lookup) and
 * {@code GET /api/stats/{code}/daily} (read-only per-day click history).
 *
 * <p>Neither mapping is secured by any filter: this codebase has no Spring
 * Security configuration, and {@link com.factory.app.ratelimit.ShortenRateLimitFilter}
 * matches only {@code POST /api/shorten} by construction, so both routes here
 * remain fully public and never rate-limited, consistent with the existing
 * stats endpoint's posture.</p>
 */
@RestController
public class StatsController {

    private final UrlShortenerService service;

    public StatsController(UrlShortenerService service) {
        this.service = service;
    }

    @GetMapping("/api/stats/{code}")
    public StatsResponse stats(@PathVariable String code) {
        UrlShortenerService.StatsResult result = service.stats(code);
        return new StatsResponse(result.code(), result.url(), result.clicks());
    }

    @GetMapping("/api/stats/{code}/daily")
    public List<DailyClickCount> daily(@PathVariable String code) {
        return service.dailyStats(code);
    }
}
