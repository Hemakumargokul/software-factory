package com.factory.app.service;

import com.factory.app.codegen.CodeGenerator;
import com.factory.app.domain.ClickEvent;
import com.factory.app.domain.UrlMapping;
import com.factory.app.repository.ClickEventRepository;
import com.factory.app.repository.UrlMappingRepository;
import java.net.URI;
import java.net.URISyntaxException;
import java.time.Instant;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Core business logic for the URL shortener: validation, dedup lookup, code
 * generation, click increment, and stats retrieval.
 */
@Service
public class UrlShortenerService {

    private static final int MAX_CODE_GENERATION_ATTEMPTS = 20;

    private final UrlMappingRepository repository;
    private final CodeGenerator codeGenerator;
    private final ClickEventRepository clickEventRepository;

    public UrlShortenerService(
            UrlMappingRepository repository,
            CodeGenerator codeGenerator,
            ClickEventRepository clickEventRepository) {
        this.repository = repository;
        this.codeGenerator = codeGenerator;
        this.clickEventRepository = clickEventRepository;
    }

    /**
     * Result of a shorten operation, distinguishing a freshly created mapping
     * (HTTP 201) from a deduplicated existing one (HTTP 200).
     */
    public record ShortenResult(String code, String url, boolean created) {
    }

    @Transactional
    public ShortenResult shorten(Object rawUrl) {
        String url = validate(rawUrl);

        return repository.findByOriginalUrl(url)
                .map(existing -> new ShortenResult(existing.getCode(), existing.getOriginalUrl(), false))
                .orElseGet(() -> createNew(url));
    }

    private ShortenResult createNew(String url) {
        DataIntegrityViolationException lastFailure = null;
        for (int attempt = 0; attempt < MAX_CODE_GENERATION_ATTEMPTS; attempt++) {
            String code = codeGenerator.nextCode();
            try {
                UrlMapping saved = repository.saveAndFlush(new UrlMapping(code, url));
                return new ShortenResult(saved.getCode(), saved.getOriginalUrl(), true);
            } catch (DataIntegrityViolationException e) {
                // Concurrent request won the race for either this code or this
                // URL. If another request already created a mapping for the
                // same URL, dedup onto it; otherwise retry with a new code.
                lastFailure = e;
                var existing = repository.findByOriginalUrl(url);
                if (existing.isPresent()) {
                    return new ShortenResult(existing.get().getCode(), existing.get().getOriginalUrl(), false);
                }
            }
        }
        throw new IllegalStateException(
                "Unable to create a short URL mapping after " + MAX_CODE_GENERATION_ATTEMPTS + " attempts",
                lastFailure);
    }

    /**
     * Looks up a code, increments its click count, and returns the original
     * URL to redirect to. Throws {@link CodeNotFoundException} if unknown.
     */
    @Transactional
    public String resolveAndRecordClick(String code) {
        UrlMapping mapping = repository.findByCode(code)
                .orElseThrow(() -> new CodeNotFoundException(code));
        mapping.incrementClickCount();
        repository.save(mapping);
        clickEventRepository.save(new ClickEvent(mapping.getCode(), Instant.now()));
        return mapping.getOriginalUrl();
    }

    /** Read-only stats lookup; never mutates click count. */
    @Transactional(readOnly = true)
    public StatsResult stats(String code) {
        UrlMapping mapping = repository.findByCode(code)
                .orElseThrow(() -> new CodeNotFoundException(code));
        return new StatsResult(mapping.getCode(), mapping.getOriginalUrl(), mapping.getClickCount());
    }

    /** Result of a stats lookup. */
    public record StatsResult(String code, String url, long clicks) {
    }

    /**
     * Validates the raw JSON value bound from the request body: must be
     * present, must be a string, must parse as a syntactically valid URI, and
     * must have an http or https scheme.
     */
    private String validate(Object rawUrl) {
        if (rawUrl == null) {
            throw new InvalidUrlException("url is required");
        }
        if (!(rawUrl instanceof String url)) {
            throw new InvalidUrlException("url must be a string");
        }
        if (url.isBlank()) {
            throw new InvalidUrlException("url must not be blank");
        }

        URI uri;
        try {
            uri = new URI(url);
        } catch (URISyntaxException e) {
            throw new InvalidUrlException("url is not a valid URI: " + e.getMessage());
        }

        String scheme = uri.getScheme();
        if (scheme == null
                || !(scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"))) {
            throw new InvalidUrlException("url scheme must be http or https");
        }
        if (uri.getHost() == null) {
            throw new InvalidUrlException("url must be an absolute URL with a host");
        }

        return url;
    }
}
