package com.factory.app.web;

import com.factory.app.service.UrlShortenerService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

/** Handles {@code GET /api/stats/{code}}: read-only click-count lookup. */
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
}
