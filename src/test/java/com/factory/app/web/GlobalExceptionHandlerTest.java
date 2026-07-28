package com.factory.app.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.factory.app.service.CodeNotFoundException;
import com.factory.app.service.InvalidUrlException;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;

/**
 * Pure unit tests for {@link GlobalExceptionHandler}, exercised directly
 * (no Spring context) to verify each handler method maps its exception to
 * the correct HTTP status and populates every {@link ErrorResponse} field.
 */
class GlobalExceptionHandlerTest {

    private final GlobalExceptionHandler handler = new GlobalExceptionHandler();

    private HttpServletRequest requestFor(String path) {
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getRequestURI()).thenReturn(path);
        when(request.getMethod()).thenReturn("GET");
        return request;
    }

    @Test
    void invalidUrlExceptionMapsTo400WithAllFieldsPopulated() {
        HttpServletRequest request = requestFor("/api/shorten");

        ResponseEntity<ErrorResponse> response =
                handler.handleInvalidUrl(new InvalidUrlException("url is required"), request);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        ErrorResponse body = response.getBody();
        assertThat(body).isNotNull();
        assertThat(body.status()).isEqualTo(400);
        assertThat(body.error()).isNotBlank();
        assertThat(body.message()).isEqualTo("url is required");
        assertThat(body.path()).isEqualTo("/api/shorten");
        assertThat(body.timestamp()).isNotNull();
    }

    @Test
    void malformedJsonMapsTo400WithGenericMessage() {
        HttpServletRequest request = requestFor("/api/shorten");
        HttpMessageNotReadableException e = mock(HttpMessageNotReadableException.class);

        ResponseEntity<ErrorResponse> response = handler.handleUnreadableBody(e, request);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        ErrorResponse body = response.getBody();
        assertThat(body).isNotNull();
        assertThat(body.status()).isEqualTo(400);
        assertThat(body.error()).isNotBlank();
        assertThat(body.message()).isNotBlank();
        assertThat(body.path()).isEqualTo("/api/shorten");
    }

    @Test
    void codeNotFoundExceptionMapsTo404WithAllFieldsPopulated() {
        HttpServletRequest request = requestFor("/api/stats/abc1234");

        ResponseEntity<ErrorResponse> response =
                handler.handleCodeNotFound(new CodeNotFoundException("abc1234"), request);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        ErrorResponse body = response.getBody();
        assertThat(body).isNotNull();
        assertThat(body.status()).isEqualTo(404);
        assertThat(body.error()).isNotBlank();
        assertThat(body.message()).contains("abc1234");
        assertThat(body.path()).isEqualTo("/api/stats/abc1234");
    }

    @Test
    void unexpectedExceptionMapsTo500WithGenericNonLeakingMessage() {
        HttpServletRequest request = requestFor("/api/shorten");
        RuntimeException e = new RuntimeException("some internal secret detail: NullPointerException at line 42");

        ResponseEntity<ErrorResponse> response = handler.handleUnexpected(e, request);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR);
        ErrorResponse body = response.getBody();
        assertThat(body).isNotNull();
        assertThat(body.status()).isEqualTo(500);
        assertThat(body.error()).isNotBlank();
        assertThat(body.message()).isNotBlank();
        // The generic message must never leak the original exception's
        // message, class name, or stack trace details.
        assertThat(body.message()).doesNotContain("secret");
        assertThat(body.message()).doesNotContain("NullPointerException");
        assertThat(body.message()).doesNotContain("RuntimeException");
        assertThat(body.path()).isEqualTo("/api/shorten");
    }
}
