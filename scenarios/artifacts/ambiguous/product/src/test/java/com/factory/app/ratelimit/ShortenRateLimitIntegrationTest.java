package com.factory.app.ratelimit;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

/**
 * End-to-end verification of the hand-rolled per-IP rate limiter wired into
 * the real Spring filter chain, exercised through MockMvc against the full
 * application context (controllers, service, repository, H2, actuator, and
 * the {@link ShortenRateLimitFilter} all wired together as the final piece
 * of the specification).
 */
@SpringBootTest
@AutoConfigureMockMvc
class ShortenRateLimitIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    private final ObjectMapper objectMapper = new ObjectMapper();

    /**
     * MockMvc requests all originate from the same simulated client
     * (default remote addr, "127.0.0.1") within this test method, so looping
     * more than 30 POSTs in the same 1-second window must start returning
     * HTTP 429 with a JSON error body once the cap is hit.
     */
    @Test
    void loopingPostShortenBeyondThirtyPerSecondReturns429() throws Exception {
        int accepted = 0;
        int rejected = 0;

        for (int i = 0; i < 45; i++) {
            String url = "https://example.com/rate-limit-loop-" + System.nanoTime();
            MvcResult result = mockMvc.perform(post("/api/shorten")
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(objectMapper.writeValueAsString(Map.of("url", url))))
                    .andReturn();

            int status = result.getResponse().getStatus();
            if (status == 429) {
                rejected++;
                assertJsonErrorBody(result);
            } else {
                accepted++;
            }

            // Stop once we've clearly observed both accepted and rejected
            // behavior so the test doesn't depend on exactly how many
            // requests from earlier tests already landed in this window.
            if (accepted > 0 && rejected > 0) {
                break;
            }
        }

        // At minimum we must eventually see a 429 once the per-IP cap for
        // this window is exhausted; if this test's own 45 iterations plus
        // whatever ran earlier in the same 1-second window didn't trip it,
        // that would indicate the filter isn't actually enforcing the limit.
        org.assertj.core.api.Assertions.assertThat(rejected)
                .as("expected at least one HTTP 429 once the per-IP shorten limit is exceeded")
                .isGreaterThan(0);
    }

    private void assertJsonErrorBody(MvcResult result) throws Exception {
        String body = result.getResponse().getContentAsString();
        var node = objectMapper.readTree(body);
        org.assertj.core.api.Assertions.assertThat(node.has("error")).isTrue();
        org.assertj.core.api.Assertions.assertThat(node.get("error").asText()).isNotBlank();
    }

    /**
     * FR6: {@link GlobalExceptionHandler} must never intercept or alter the
     * rate limiter's own 429 response. The filter runs before
     * DispatcherServlet and writes its raw {@code {"error": "..."}} body
     * directly, so a 429 response must have exactly that shape -- no
     * {@code status}/{@code message}/{@code path}/{@code timestamp} fields
     * that the structured {@link com.factory.app.web.ErrorResponse} would
     * add if this request had instead gone through the global advice.
     */
    @Test
    void rateLimitResponseBodyRemainsRawAndUnstructuredByGlobalExceptionHandler() throws Exception {
        MvcResult rejected = null;
        for (int i = 0; i < 45 && rejected == null; i++) {
            String url = "https://example.com/rate-limit-raw-shape-" + System.nanoTime();
            MvcResult result = mockMvc.perform(post("/api/shorten")
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(objectMapper.writeValueAsString(Map.of("url", url))))
                    .andReturn();
            if (result.getResponse().getStatus() == 429) {
                rejected = result;
            }
        }

        org.assertj.core.api.Assertions.assertThat(rejected)
                .as("expected to observe at least one 429 to assert its raw body shape")
                .isNotNull();

        org.assertj.core.api.Assertions.assertThat(rejected.getResponse().getContentType())
                .contains("application/json");

        var node = objectMapper.readTree(rejected.getResponse().getContentAsString());
        org.assertj.core.api.Assertions.assertThat(node.has("error")).isTrue();
        org.assertj.core.api.Assertions.assertThat(node.get("error").asText()).isNotBlank();

        // The filter's raw body must be exactly the one-field shape it
        // writes itself -- none of GlobalExceptionHandler/ErrorResponse's
        // extra fields should be present, proving the advice never touched
        // this response.
        org.assertj.core.api.Assertions.assertThat(node.has("status")).isFalse();
        org.assertj.core.api.Assertions.assertThat(node.has("message")).isFalse();
        org.assertj.core.api.Assertions.assertThat(node.has("path")).isFalse();
        org.assertj.core.api.Assertions.assertThat(node.has("timestamp")).isFalse();
        org.assertj.core.api.Assertions.assertThat(node.size()).isEqualTo(1);
    }

    /**
     * FR12 sanity check: even after saturating the POST /api/shorten limit
     * for this client, GET /{code}, GET /api/stats/{code}, and
     * GET /actuator/health must remain fully functional and never return 429.
     */
    @Test
    void excludedEndpointsRemainUnaffectedUnderSaturatedRateLimit() throws Exception {
        // First, create a mapping to redirect/stat against, before we
        // saturate the limiter for this client.
        String url = "https://example.com/rate-limit-sanity-" + System.nanoTime();
        MvcResult created = mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", url))))
                .andReturn();
        String code = objectMapper.readTree(created.getResponse().getContentAsString()).get("code").asText();

        // Saturate the POST /api/shorten window for this client (up to 40
        // attempts is comfortably beyond the 30 cap even accounting for the
        // one request already issued above).
        for (int i = 0; i < 40; i++) {
            String loopUrl = "https://example.com/rate-limit-sanity-fill-" + System.nanoTime() + "-" + i;
            mockMvc.perform(post("/api/shorten")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(objectMapper.writeValueAsString(Map.of("url", loopUrl))));
        }

        // GET /{code} must still work: 302 with Location header, unaffected
        // by the exhausted shorten-endpoint counter.
        mockMvc.perform(get("/{code}", code))
                .andExpect(status().isFound());

        // GET /api/stats/{code} must still work.
        mockMvc.perform(get("/api/stats/{code}", code))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").exists());

        // GET /actuator/health must still work.
        mockMvc.perform(get("/actuator/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UP"));

        // Repeat once more to make doubly sure none of the excluded
        // endpoints ever produce a 429 even after further calls.
        mockMvc.perform(get("/{code}", code)).andExpect(status().isFound());
        mockMvc.perform(get("/api/stats/{code}", code)).andExpect(status().isOk());
        mockMvc.perform(get("/actuator/health")).andExpect(status().isOk());
    }
}
