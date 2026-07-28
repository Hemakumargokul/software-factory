package com.factory.app.web;

import java.time.Instant;

/**
 * Structured JSON error body returned by {@link GlobalExceptionHandler} for
 * every handled failure (malformed request bodies, validation failures,
 * unknown short codes, and unexpected server errors).
 *
 * <p>{@code error} is kept as the primary human-readable reason and mirrors
 * {@code message} for backward compatibility with existing clients/tests
 * that only assert on {@code $.error}. {@code timestamp}, {@code status},
 * {@code message}, and {@code path} satisfy FR4 of the error-handling
 * specification.</p>
 */
public record ErrorResponse(Instant timestamp, int status, String error, String message, String path) {

    /**
     * Convenience factory that stamps the current time and derives both
     * {@code error} (the reason phrase, e.g. "Bad Request") and
     * {@code message} (the human-readable detail) consistently.
     */
    public static ErrorResponse of(int status, String reason, String message, String path) {
        return new ErrorResponse(Instant.now(), status, reason, message, path);
    }
}
