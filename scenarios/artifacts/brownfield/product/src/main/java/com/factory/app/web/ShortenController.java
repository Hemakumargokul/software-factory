package com.factory.app.web;

import com.factory.app.service.UrlShortenerService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/** Handles {@code POST /api/shorten}: create-or-dedup a short code for a URL. */
@RestController
public class ShortenController {

    private final UrlShortenerService service;

    public ShortenController(UrlShortenerService service) {
        this.service = service;
    }

    @PostMapping("/api/shorten")
    public ResponseEntity<ShortenResponse> shorten(@RequestBody(required = false) ShortenRequest request) {
        Object rawUrl = request == null ? null : request.getUrl();
        UrlShortenerService.ShortenResult result = service.shorten(rawUrl);

        HttpStatus status = result.created() ? HttpStatus.CREATED : HttpStatus.OK;
        return ResponseEntity.status(status).body(new ShortenResponse(result.code(), result.url()));
    }
}
