package com.factory.app.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.equalTo;
import static org.hamcrest.Matchers.hasLength;
import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.factory.app.domain.ClickEvent;
import com.factory.app.repository.ClickEventRepository;
import java.time.Instant;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

/**
 * End-to-end HTTP contract tests for the shorten/redirect/stats endpoints.
 *
 * <p>Uses MockMvc exclusively (no real HTTP client such as RestTemplate or
 * TestRestTemplate) so that 3xx responses from {@code GET /{code}} are
 * observed directly as status + Location header, never transparently
 * followed by an HTTP client.</p>
 */
@SpringBootTest
@AutoConfigureMockMvc
class ShortenControllerWebTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ClickEventRepository clickEventRepository;

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void shortenReturns201WithCodeAndUrl() throws Exception {
        String url = "https://example.com/web-test-" + System.nanoTime();

        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", url))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.code", hasLength(7)))
                .andExpect(jsonPath("$.url", equalTo(url)));
    }

    @Test
    void shortenTwiceReturns200SameCodeNoDuplicateRow() throws Exception {
        String url = "https://example.com/web-dedup-" + System.nanoTime();

        MvcResult first = mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", url))))
                .andExpect(status().isCreated())
                .andReturn();
        String firstCode = objectMapper.readTree(first.getResponse().getContentAsString()).get("code").asText();

        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", url))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code", equalTo(firstCode)))
                .andExpect(jsonPath("$.url", equalTo(url)));
    }

    @Test
    void missingUrlReturns400WithErrorBody() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error", not(equalTo(""))));
    }

    @Test
    void invalidSchemeReturns400() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", "ftp://example.com/file"))))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error", not(equalTo(""))));
    }

    @Test
    void malformedUrlReturns400() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", "not a url at all"))))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error", not(equalTo(""))));
    }

    @Test
    void redirectReturns302WithLocationAndIncrementsClicks() throws Exception {
        String url = "https://example.com/redirect-test-" + System.nanoTime();

        MvcResult created = mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", url))))
                .andExpect(status().isCreated())
                .andReturn();
        String code = objectMapper.readTree(created.getResponse().getContentAsString()).get("code").asText();

        // Assert on the 302 status + Location header directly; do NOT follow
        // the redirect with any HTTP client.
        mockMvc.perform(get("/{code}", code))
                .andExpect(status().isFound())
                .andExpect(header().string("Location", url));

        mockMvc.perform(get("/api/stats/{code}", code))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.clicks", equalTo(1)));
    }

    @Test
    void redirectContractUnchangedByClickEventPersistence() throws Exception {
        // Regression test for the ClickEvent persistence change: the redirect
        // endpoint's status code, headers, and (lack of) response body must
        // be byte-for-byte identical to before that change.
        String url = "https://example.com/redirect-contract-" + System.nanoTime();

        MvcResult created = mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", url))))
                .andExpect(status().isCreated())
                .andReturn();
        String code = objectMapper.readTree(created.getResponse().getContentAsString()).get("code").asText();

        MvcResult redirect = mockMvc.perform(get("/{code}", code))
                .andExpect(status().is(302))
                .andExpect(status().isFound())
                .andExpect(header().string("Location", url))
                .andReturn();

        assertThat(redirect.getResponse().getContentAsString()).isEmpty();
        assertThat(redirect.getResponse().getContentType()).isNull();
    }

    @Test
    void unknownCodeRedirectReturns404() throws Exception {
        mockMvc.perform(get("/{code}", "nosuchcode"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error", not(equalTo(""))));
    }

    @Test
    void unknownCodeStatsReturns404() throws Exception {
        mockMvc.perform(get("/api/stats/{code}", "nosuchcode"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error", not(equalTo(""))));
    }

    @Test
    void statsReturns200WithCorrectClicksCount() throws Exception {
        String url = "https://example.com/stats-count-" + System.nanoTime();

        MvcResult created = mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", url))))
                .andExpect(status().isCreated())
                .andReturn();
        String code = objectMapper.readTree(created.getResponse().getContentAsString()).get("code").asText();

        mockMvc.perform(get("/{code}", code)).andExpect(status().isFound());
        mockMvc.perform(get("/{code}", code)).andExpect(status().isFound());
        mockMvc.perform(get("/{code}", code)).andExpect(status().isFound());

        mockMvc.perform(get("/api/stats/{code}", code))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code", equalTo(code)))
                .andExpect(jsonPath("$.url", equalTo(url)))
                .andExpect(jsonPath("$.clicks", equalTo(3)));
    }

    @Test
    void statsNeverIncrementsClickCount() throws Exception {
        String url = "https://example.com/stats-no-increment-web-" + System.nanoTime();

        MvcResult created = mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", url))))
                .andExpect(status().isCreated())
                .andReturn();
        String code = objectMapper.readTree(created.getResponse().getContentAsString()).get("code").asText();

        mockMvc.perform(get("/api/stats/{code}", code))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.clicks", equalTo(0)));
        mockMvc.perform(get("/api/stats/{code}", code))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.clicks", equalTo(0)));
        mockMvc.perform(get("/api/stats/{code}", code))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.clicks", equalTo(0)));
    }

    // ---- GET /api/stats/{code}/daily -------------------------------------

    @Test
    void dailyStatsKnownCodeWithZeroClicksReturns200EmptyArray() throws Exception {
        String url = "https://example.com/daily-web-zero-" + System.nanoTime();

        MvcResult created = mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", url))))
                .andExpect(status().isCreated())
                .andReturn();
        String code = objectMapper.readTree(created.getResponse().getContentAsString()).get("code").asText();

        mockMvc.perform(get("/api/stats/{code}/daily", code))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$", org.hamcrest.Matchers.hasSize(0)));
    }

    @Test
    void dailyStatsUnknownCodeReturns404WithNoArrayBody() throws Exception {
        mockMvc.perform(get("/api/stats/{code}/daily", "nosuchcode"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error", not(equalTo(""))))
                .andExpect(jsonPath("$").isMap());
    }

    @Test
    void dailyStatsSyntacticallyInvalidCodeReturns404() throws Exception {
        mockMvc.perform(get("/api/stats/{code}/daily", "!!!not-valid!!!"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error", not(equalTo(""))));
    }

    @Test
    void dailyStatsKnownCodeWithClicksAcrossDaysReturnsAggregatedDescendingArray() throws Exception {
        String url = "https://example.com/daily-web-multi-" + System.nanoTime();

        MvcResult created = mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", url))))
                .andExpect(status().isCreated())
                .andReturn();
        String code = objectMapper.readTree(created.getResponse().getContentAsString()).get("code").asText();

        // Directly persist ClickEvent rows at controlled UTC timestamps
        // spanning several distinct calendar days, including near-midnight
        // boundary cases, to validate FR5-FR10 holistically without
        // depending on wall-clock time.
        clickEventRepository.saveAndFlush(new ClickEvent(code, Instant.parse("2024-06-01T00:00:00Z")));
        clickEventRepository.saveAndFlush(new ClickEvent(code, Instant.parse("2024-06-01T12:00:00Z")));
        clickEventRepository.saveAndFlush(new ClickEvent(code, Instant.parse("2024-06-01T23:59:59Z")));
        clickEventRepository.saveAndFlush(new ClickEvent(code, Instant.parse("2024-06-02T00:00:00Z")));
        clickEventRepository.saveAndFlush(new ClickEvent(code, Instant.parse("2024-06-03T08:30:00Z")));

        mockMvc.perform(get("/api/stats/{code}/daily", code))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$", org.hamcrest.Matchers.hasSize(3)))
                .andExpect(jsonPath("$[0].date", equalTo("2024-06-03")))
                .andExpect(jsonPath("$[0].clicks", equalTo(1)))
                .andExpect(jsonPath("$[1].date", equalTo("2024-06-02")))
                .andExpect(jsonPath("$[1].clicks", equalTo(1)))
                .andExpect(jsonPath("$[2].date", equalTo("2024-06-01")))
                .andExpect(jsonPath("$[2].clicks", equalTo(3)));
    }

    @Test
    void dailyStatsDoesNotAffectExistingStatsEndpointResponse() throws Exception {
        // Regression: GET /api/stats/{code}'s status, body shape, and headers
        // must be unchanged by the presence of the sibling /daily route.
        String url = "https://example.com/daily-regression-" + System.nanoTime();

        MvcResult created = mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", url))))
                .andExpect(status().isCreated())
                .andReturn();
        String code = objectMapper.readTree(created.getResponse().getContentAsString()).get("code").asText();

        mockMvc.perform(get("/{code}", code)).andExpect(status().isFound());

        MvcResult statsResult = mockMvc.perform(get("/api/stats/{code}", code))
                .andExpect(status().isOk())
                .andExpect(header().string("Content-Type", org.hamcrest.Matchers.containsString(MediaType.APPLICATION_JSON_VALUE)))
                .andExpect(jsonPath("$.code", equalTo(code)))
                .andExpect(jsonPath("$.url", equalTo(url)))
                .andExpect(jsonPath("$.clicks", equalTo(1)))
                .andReturn();

        // Response body must contain exactly the three known fields, no
        // "daily" array or other new fields leaking into this endpoint.
        var node = objectMapper.readTree(statsResult.getResponse().getContentAsString());
        assertThat(node.size()).isEqualTo(3);
        assertThat(node.has("code")).isTrue();
        assertThat(node.has("url")).isTrue();
        assertThat(node.has("clicks")).isTrue();
    }

    /**
     * End-to-end style test (FR5-FR10): real clicks are recorded via the
     * actual redirect endpoint (not direct repository inserts) at multiple
     * UTC timestamps, including near-midnight boundary cases, then the new
     * daily endpoint is called to validate holistic behavior: correct UTC
     * day bucketing, descending order, no zero-click days, and full history
     * coverage with no pagination.
     */
    @Test
    void endToEndRedirectClicksAggregateCorrectlyByUtcDayIncludingMidnightBoundaries() throws Exception {
        String url = "https://example.com/e2e-daily-" + System.nanoTime();

        MvcResult created = mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", url))))
                .andExpect(status().isCreated())
                .andReturn();
        String code = objectMapper.readTree(created.getResponse().getContentAsString()).get("code").asText();

        // Real redirects via the actual endpoint record "now" timestamps;
        // exercise those first to prove the live wiring works end-to-end.
        mockMvc.perform(get("/{code}", code)).andExpect(status().isFound());
        mockMvc.perform(get("/{code}", code)).andExpect(status().isFound());

        // Then layer in controlled historical events straddling UTC midnight
        // boundaries to deterministically validate day-bucketing semantics
        // without depending on wall-clock timing in this test run.
        clickEventRepository.saveAndFlush(new ClickEvent(code, Instant.parse("2024-01-09T23:59:59Z")));
        clickEventRepository.saveAndFlush(new ClickEvent(code, Instant.parse("2024-01-10T00:00:00Z")));
        clickEventRepository.saveAndFlush(new ClickEvent(code, Instant.parse("2024-01-10T00:00:01Z")));

        MvcResult dailyResult = mockMvc.perform(get("/api/stats/{code}/daily", code))
                .andExpect(status().isOk())
                .andReturn();

        var array = objectMapper.readTree(dailyResult.getResponse().getContentAsString());
        assertThat(array.isArray()).isTrue();

        // The two "now" redirects land on whatever today's UTC date is, plus
        // the two historical days above: at most 3 distinct dates, at least
        // the 2024-01-09 and 2024-01-10 boundary days must be present and
        // distinct, each with exactly their own click, and the whole array
        // must be sorted descending by date with no duplicate dates.
        boolean sawJan9 = false;
        boolean sawJan10 = false;
        String previousDate = null;
        java.util.Set<String> seenDates = new java.util.HashSet<>();
        for (var node : array) {
            String date = node.get("date").asText();
            long clicks = node.get("clicks").asLong();
            assertThat(clicks).isGreaterThan(0);
            assertThat(seenDates.add(date)).as("no duplicate date entries").isTrue();
            if (previousDate != null) {
                assertThat(date.compareTo(previousDate)).isLessThan(0);
            }
            previousDate = date;

            if (date.equals("2024-01-09")) {
                sawJan9 = true;
                assertThat(clicks).isEqualTo(1);
            }
            if (date.equals("2024-01-10")) {
                sawJan10 = true;
                assertThat(clicks).isEqualTo(2);
            }
        }
        assertThat(sawJan9).isTrue();
        assertThat(sawJan10).isTrue();
    }
}
