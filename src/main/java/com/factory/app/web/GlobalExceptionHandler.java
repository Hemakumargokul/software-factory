package com.factory.app.web;

import com.factory.app.service.CodeNotFoundException;
import com.factory.app.service.InvalidUrlException;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * Central translator from controller/service-layer exceptions to a uniform
 * structured {@link ErrorResponse} JSON body with the correct HTTP status.
 *
 * <p>Handles malformed JSON request bodies ({@link HttpMessageNotReadableException}
 * -&gt; 400), validation failures ({@link InvalidUrlException} -&gt; 400), unknown
 * short codes ({@link CodeNotFoundException} -&gt; 404), and a catch-all for any
 * other unhandled exception (-&gt; 500) with a generic, non-leaking message.</p>
 *
 * <p>This advice only intercepts exceptions that propagate out of
 * {@code DispatcherServlet}-dispatched controller methods. {@link
 * com.factory.app.ratelimit.ShortenRateLimitFilter} runs as a servlet filter
 * before dispatch and writes its 429 response directly, so it is structurally
 * isolated from this class and its behavior is completely unaffected
 * (FR6).</p>
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);
    private static final String GENERIC_SERVER_ERROR_MESSAGE =
            "An unexpected error occurred. Please try again later.";

    @ExceptionHandler(InvalidUrlException.class)
    public ResponseEntity<ErrorResponse> handleInvalidUrl(InvalidUrlException e, HttpServletRequest request) {
        return build(HttpStatus.BAD_REQUEST, e.getMessage(), request);
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ErrorResponse> handleUnreadableBody(
            HttpMessageNotReadableException e, HttpServletRequest request) {
        return build(HttpStatus.BAD_REQUEST, "Malformed request body", request);
    }

    @ExceptionHandler(CodeNotFoundException.class)
    public ResponseEntity<ErrorResponse> handleCodeNotFound(CodeNotFoundException e, HttpServletRequest request) {
        return build(HttpStatus.NOT_FOUND, e.getMessage(), request);
    }

    /**
     * Catch-all safety net for any exception not handled above. Logs the
     * full exception server-side for diagnosability but returns only a
     * generic message to the client, never a stack trace or internal class
     * name (FR3).
     */
    @ExceptionHandler(Exception.class)
    public ResponseEntity<ErrorResponse> handleUnexpected(Exception e, HttpServletRequest request) {
        log.error("Unhandled exception while processing {} {}", request.getMethod(), request.getRequestURI(), e);
        return build(HttpStatus.INTERNAL_SERVER_ERROR, GENERIC_SERVER_ERROR_MESSAGE, request);
    }

    private ResponseEntity<ErrorResponse> build(HttpStatus status, String message, HttpServletRequest request) {
        ErrorResponse body = ErrorResponse.of(status.value(), status.getReasonPhrase(), message,
                request.getRequestURI());
        return ResponseEntity.status(status).body(body);
    }
}
