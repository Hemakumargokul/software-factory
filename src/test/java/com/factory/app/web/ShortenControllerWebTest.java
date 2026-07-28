package com.factory.app.web;

import static org.hamcrest.Matchers.equalTo;
import static org.hamcrest.Matchers.hasLength;
import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
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
}
