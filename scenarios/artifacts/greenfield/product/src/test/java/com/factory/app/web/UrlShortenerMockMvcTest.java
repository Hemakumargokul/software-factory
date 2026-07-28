package com.factory.app.web;

import static org.hamcrest.Matchers.matchesPattern;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/**
 * MockMvc/@SpringBootTest coverage for the full request/response contract of
 * {@code POST /api/shorten}, {@code GET /{code}}, and
 * {@code GET /api/stats/{code}}: creation, dedup, every FR3 validation-400
 * case, redirect semantics (including click increment and the no-mutation
 * guarantee on a miss), and stats semantics (including the never-increments
 * guarantee).
 */
@SpringBootTest
@AutoConfigureMockMvc
class UrlShortenerMockMvcTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    // ------------------------------------------------------------------
    // POST /api/shorten: creation + dedup
    // ------------------------------------------------------------------

    @Test
    void createNewUrlReturns201WithCodeAndUrl() throws Exception {
        String url = "https://example.com/mockmvc-create-" + System.nanoTime();

        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(shortenBody(url)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.code", matchesPattern("^[0-9A-Za-z]{7}$")))
                .andExpect(jsonPath("$.url").value(url));
    }

    @Test
    void exactDedupReturns200WithSameCodeAndNoDuplicateRow() throws Exception {
        String url = "https://example.com/mockmvc-dedup-" + System.nanoTime();

        String firstBody = mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(shortenBody(url)))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        String firstCode = objectMapper.readTree(firstBody).get("code").asText();

        // Repeat the exact same request several times; every repeat must dedup
        // onto the same code with 200, never minting a new mapping.
        for (int i = 0; i < 3; i++) {
            mockMvc.perform(post("/api/shorten")
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(shortenBody(url)))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.code").value(firstCode))
                    .andExpect(jsonPath("$.url").value(url));
        }
    }

    // ------------------------------------------------------------------
    // POST /api/shorten: FR3 validation failures -> 400 {"error": ...}
    // ------------------------------------------------------------------

    @Test
    void missingUrlFieldReturns400() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    @Test
    void nullUrlFieldReturns400() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"url\": null}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    @Test
    void nonStringNumericUrlReturns400() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"url\": 12345}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    @Test
    void nonStringObjectUrlReturns400() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"url\": {\"nested\": true}}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    @Test
    void nonStringArrayUrlReturns400() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"url\": [\"https://example.com\"]}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    @Test
    void malformedJsonBodyReturns400() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{not valid json"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    @Test
    void emptyBodyReturns400() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(""))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    @Test
    void malformedUriSyntaxReturns400() throws Exception {
        // Unescaped "^" is not valid syntax per RFC 3986 / java.net.URI.
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(shortenBody("http://example.com/^bad^path")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    @Test
    void noSchemeUrlReturns400() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(shortenBody("example.com/no-scheme")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "ftp://example.com/file.txt",
            "file:///etc/passwd",
            "mailto:someone@example.com"
    })
    void disallowedSchemesReturn400(String url) throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(shortenBody(url)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    // ------------------------------------------------------------------
    // GET /{code}: redirect semantics
    // ------------------------------------------------------------------

    @Test
    void redirectReturns302WithExactLocationAndIncrementsClickCount() throws Exception {
        String url = "https://example.com/mockmvc-redirect-" + System.nanoTime();
        String code = createAndGetCode(url);

        mockMvc.perform(get("/" + code))
                .andExpect(status().isFound())
                .andExpect(header().string("Location", url));

        mockMvc.perform(get("/api/stats/" + code))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.clicks").value(1));

        // A second redirect increments again.
        mockMvc.perform(get("/" + code))
                .andExpect(status().isFound())
                .andExpect(header().string("Location", url));

        mockMvc.perform(get("/api/stats/" + code))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.clicks").value(2));
    }

    @Test
    void redirectUnknownCodeReturns404AndDoesNotCreateOrMutateAnything() throws Exception {
        String unknownCode = "zzzzz99";

        mockMvc.perform(get("/" + unknownCode))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").isNotEmpty());

        // Still unknown afterwards: no phantom row/click was created by the miss.
        mockMvc.perform(get("/api/stats/" + unknownCode))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    // ------------------------------------------------------------------
    // GET /api/stats/{code}: read-only semantics
    // ------------------------------------------------------------------

    @Test
    void statsReturns200WithCorrectClicksOnHit() throws Exception {
        String url = "https://example.com/mockmvc-stats-" + System.nanoTime();
        String code = createAndGetCode(url);

        mockMvc.perform(get("/api/stats/" + code))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(code))
                .andExpect(jsonPath("$.url").value(url))
                .andExpect(jsonPath("$.clicks").value(0));
    }

    @Test
    void statsReturns404ForUnknownCode() throws Exception {
        mockMvc.perform(get("/api/stats/nosuch1"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    @Test
    void statsNeverIncrementsAcrossRepeatedCalls() throws Exception {
        String url = "https://example.com/mockmvc-stats-no-incr-" + System.nanoTime();
        String code = createAndGetCode(url);

        for (int i = 0; i < 5; i++) {
            mockMvc.perform(get("/api/stats/" + code))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.clicks").value(0));
        }
    }

    // ------------------------------------------------------------------
    // helpers
    // ------------------------------------------------------------------

    private String shortenBody(String url) throws Exception {
        return objectMapper.writeValueAsString(Map.of("url", url));
    }

    private String createAndGetCode(String url) throws Exception {
        String body = mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(shortenBody(url)))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(body).get("code").asText();
    }
}
