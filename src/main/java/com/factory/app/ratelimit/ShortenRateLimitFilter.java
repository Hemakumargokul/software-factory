package com.factory.app.ratelimit;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Hand-rolled per-IP rate limiter for {@code POST /api/shorten} only.
 *
 * <p>Uses a fixed 1-second window keyed by {@code System.currentTimeMillis() / 1000}.
 * Each client IP (as reported by {@link HttpServletRequest#getRemoteAddr()})
 * gets a counter that resets whenever the request's window differs from the
 * counter's current window. Up to 30 requests are accepted per IP per
 * window; the 31st+ gets an immediate HTTP 429 JSON error and the filter
 * chain is halted. Stale per-IP entries are evicted opportunistically so
 * memory doesn't grow unbounded across many distinct client IPs.</p>
 *
 * <p><strong>Not wired into the application in this task.</strong> Rate
 * limiting is out of scope for the current deliverable (project skeleton,
 * persistence, and core shorten/redirect/stats logic); this class
 * intentionally omits the {@code @Component} annotation so Spring does not
 * register it as a servlet filter. A later task is expected to wire it in
 * (e.g. via {@code @Component} or an explicit {@code FilterRegistrationBean})
 * once rate limiting is in scope.</p>
 */
public class ShortenRateLimitFilter extends OncePerRequestFilter {

    private static final int MAX_REQUESTS_PER_WINDOW = 30;
    private static final long WINDOW_MILLIS = 1000L;
    /** Evict an IP's counter once it hasn't been touched for this many windows. */
    private static final long STALE_AFTER_WINDOWS = 10;

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final Map<String, WindowCounter> counters = new ConcurrentHashMap<>();

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {

        if (isRateLimited(request)) {
            String ip = request.getRemoteAddr();
            long currentWindow = currentWindow();
            WindowCounter counter = counters.computeIfAbsent(ip, k -> new WindowCounter(currentWindow));

            if (!counter.tryAccept(currentWindow)) {
                evictStale(currentWindow);
                writeTooManyRequests(response);
                return;
            }
            evictStale(currentWindow);
        }

        chain.doFilter(request, response);
    }

    private boolean isRateLimited(HttpServletRequest request) {
        return "POST".equalsIgnoreCase(request.getMethod()) && "/api/shorten".equals(request.getRequestURI());
    }

    private long currentWindow() {
        return System.currentTimeMillis() / WINDOW_MILLIS;
    }

    private void writeTooManyRequests(HttpServletResponse response) throws IOException {
        response.setStatus(429);
        response.setContentType("application/json");
        response.getWriter().write(objectMapper.writeValueAsString(
                Map.of("error", "Rate limit exceeded: max " + MAX_REQUESTS_PER_WINDOW + " requests per second")));
    }

    private void evictStale(long currentWindow) {
        counters.entrySet().removeIf(e -> currentWindow - e.getValue().windowRef() > STALE_AFTER_WINDOWS);
    }

    /** Per-IP counter for the currently active fixed window. */
    private static final class WindowCounter {
        private final AtomicLong window;
        private final AtomicInteger count = new AtomicInteger(0);

        WindowCounter(long initialWindow) {
            this.window = new AtomicLong(initialWindow);
        }

        long windowRef() {
            return window.get();
        }

        /**
         * Atomically rolls the window forward if it's stale, then attempts to
         * accept one more request in the current window.
         */
        synchronized boolean tryAccept(long currentWindow) {
            if (window.get() != currentWindow) {
                window.set(currentWindow);
                count.set(0);
            }
            if (count.get() >= MAX_REQUESTS_PER_WINDOW) {
                return false;
            }
            count.incrementAndGet();
            return true;
        }
    }
}
