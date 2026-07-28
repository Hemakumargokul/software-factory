package com.factory.app.ratelimit;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class ShortenRateLimitFilterTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void allowsUpToThirtyRequestsPerSecondThenRejects() throws Exception {
        ShortenRateLimitFilter filter = new ShortenRateLimitFilter();
        AtomicInteger passedThrough = new AtomicInteger(0);
        FilterChain chain = (req, res) -> passedThrough.incrementAndGet();

        int accepted = 0;
        int rejected = 0;
        for (int i = 0; i < 60; i++) {
            MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/shorten");
            request.setRemoteAddr("10.0.0.1");
            MockHttpServletResponse response = new MockHttpServletResponse();

            filter.doFilter(request, response, chain);

            if (response.getStatus() == 429) {
                rejected++;
            } else {
                accepted++;
            }
        }

        assertThat(accepted).isEqualTo(30);
        assertThat(rejected).isEqualTo(30);
        assertThat(passedThrough.get()).isEqualTo(30);
    }

    @Test
    void firstThirtyRequestsPassThroughThenThirtyFirstReturns429JsonAndChainHalted() throws Exception {
        ShortenRateLimitFilter filter = new ShortenRateLimitFilter();
        AtomicInteger passedThrough = new AtomicInteger(0);
        FilterChain chain = (req, res) -> passedThrough.incrementAndGet();

        for (int i = 1; i <= 30; i++) {
            MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/shorten");
            request.setRemoteAddr("10.0.0.10");
            MockHttpServletResponse response = new MockHttpServletResponse();

            filter.doFilter(request, response, chain);

            assertThat(response.getStatus()).isNotEqualTo(429);
        }
        assertThat(passedThrough.get()).isEqualTo(30);

        // 31st request from the same IP within the same window must be rejected.
        MockHttpServletRequest thirtyFirstRequest = new MockHttpServletRequest("POST", "/api/shorten");
        thirtyFirstRequest.setRemoteAddr("10.0.0.10");
        MockHttpServletResponse thirtyFirstResponse = new MockHttpServletResponse();

        filter.doFilter(thirtyFirstRequest, thirtyFirstResponse, chain);

        assertThat(thirtyFirstResponse.getStatus()).isEqualTo(429);
        assertThat(thirtyFirstResponse.getContentType()).contains("application/json");
        JsonNode body = objectMapper.readTree(thirtyFirstResponse.getContentAsString());
        assertThat(body.has("error")).isTrue();
        assertThat(body.get("error").asText()).isNotBlank();
        // Chain must not have been invoked for the rejected 31st request: still 30.
        assertThat(passedThrough.get()).isEqualTo(30);
    }

    @Test
    void windowResetsAfterOneSecondAllowingFurtherRequests() throws Exception {
        ShortenRateLimitFilter filter = new ShortenRateLimitFilter();
        FilterChain chain = (req, res) -> {
        };

        for (int i = 0; i < 30; i++) {
            MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/shorten");
            request.setRemoteAddr("10.0.0.20");
            MockHttpServletResponse response = new MockHttpServletResponse();
            filter.doFilter(request, response, chain);
            assertThat(response.getStatus()).isNotEqualTo(429);
        }

        MockHttpServletRequest overLimitRequest = new MockHttpServletRequest("POST", "/api/shorten");
        overLimitRequest.setRemoteAddr("10.0.0.20");
        MockHttpServletResponse overLimitResponse = new MockHttpServletResponse();
        filter.doFilter(overLimitRequest, overLimitResponse, chain);
        assertThat(overLimitResponse.getStatus()).isEqualTo(429);

        // Wait for the fixed 1-second window to roll over.
        Thread.sleep(1100);

        MockHttpServletRequest afterResetRequest = new MockHttpServletRequest("POST", "/api/shorten");
        afterResetRequest.setRemoteAddr("10.0.0.20");
        MockHttpServletResponse afterResetResponse = new MockHttpServletResponse();
        filter.doFilter(afterResetRequest, afterResetResponse, chain);

        assertThat(afterResetResponse.getStatus()).isNotEqualTo(429);
    }

    @Test
    void differentIpsHaveIndependentLimits() throws Exception {
        ShortenRateLimitFilter filter = new ShortenRateLimitFilter();
        FilterChain chain = (req, res) -> {
        };

        for (int i = 0; i < 30; i++) {
            MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/shorten");
            request.setRemoteAddr("10.0.0.2");
            MockHttpServletResponse response = new MockHttpServletResponse();
            filter.doFilter(request, response, chain);
            assertThat(response.getStatus()).isNotEqualTo(429);
        }

        MockHttpServletRequest otherIpRequest = new MockHttpServletRequest("POST", "/api/shorten");
        otherIpRequest.setRemoteAddr("10.0.0.3");
        MockHttpServletResponse otherIpResponse = new MockHttpServletResponse();
        filter.doFilter(otherIpRequest, otherIpResponse, chain);

        assertThat(otherIpResponse.getStatus()).isNotEqualTo(429);
    }

    @Test
    void nonShortenPathsAreNeverRateLimited() throws Exception {
        ShortenRateLimitFilter filter = new ShortenRateLimitFilter();
        FilterChain chain = (req, res) -> {
        };

        for (int i = 0; i < 100; i++) {
            MockHttpServletRequest request = new MockHttpServletRequest("GET", "/abc1234");
            request.setRemoteAddr("10.0.0.4");
            MockHttpServletResponse response = new MockHttpServletResponse();
            filter.doFilter(request, response, chain);
            assertThat(response.getStatus()).isNotEqualTo(429);
        }
    }

    @Test
    void getRequestsToShortenPathAreNotRateLimited() throws Exception {
        ShortenRateLimitFilter filter = new ShortenRateLimitFilter();
        FilterChain chain = (req, res) -> {
        };

        for (int i = 0; i < 60; i++) {
            MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/shorten");
            request.setRemoteAddr("10.0.0.5");
            MockHttpServletResponse response = new MockHttpServletResponse();
            filter.doFilter(request, response, chain);
            assertThat(response.getStatus()).isNotEqualTo(429);
        }
    }

    /**
     * FR12: GET /{code}, GET /api/stats/{code}, and GET /actuator/health must
     * never be rate-limited, even when the calling IP has already exhausted
     * its budget on POST /api/shorten within the current window.
     */
    @Test
    void excludedGetEndpointsBypassFilterEvenWhenSameIpSaturatedShortenLimit() throws Exception {
        ShortenRateLimitFilter filter = new ShortenRateLimitFilter();
        AtomicInteger passedThrough = new AtomicInteger(0);
        FilterChain chain = (req, res) -> passedThrough.incrementAndGet();
        String ip = "10.0.0.6";

        // Saturate the POST /api/shorten limit for this IP.
        for (int i = 0; i < 31; i++) {
            MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/shorten");
            request.setRemoteAddr(ip);
            MockHttpServletResponse response = new MockHttpServletResponse();
            filter.doFilter(request, response, chain);
        }
        int passedBeforeExcludedChecks = passedThrough.get();
        assertThat(passedBeforeExcludedChecks).isEqualTo(30);

        String[] excludedPaths = {"/abc1234", "/api/stats/abc1234", "/actuator/health"};
        for (String path : excludedPaths) {
            for (int i = 0; i < 5; i++) {
                MockHttpServletRequest request = new MockHttpServletRequest("GET", path);
                request.setRemoteAddr(ip);
                MockHttpServletResponse response = new MockHttpServletResponse();
                filter.doFilter(request, response, chain);
                assertThat(response.getStatus()).isNotEqualTo(429);
            }
        }

        // All excluded-path requests must have passed through the chain.
        assertThat(passedThrough.get()).isEqualTo(passedBeforeExcludedChecks + excludedPaths.length * 5);
    }
}
