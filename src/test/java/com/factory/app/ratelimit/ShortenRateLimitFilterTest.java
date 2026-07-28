package com.factory.app.ratelimit;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.servlet.FilterChain;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class ShortenRateLimitFilterTest {

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
}
