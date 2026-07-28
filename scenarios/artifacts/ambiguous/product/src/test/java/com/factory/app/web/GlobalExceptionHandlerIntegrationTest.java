package com.factory.app.web;

import static org.hamcrest.Matchers.equalTo;
import static org.hamcrest.Matchers.not;
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
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * MockMvc-based integration coverage of {@link GlobalExceptionHandler} wired
 * into the full application context, hitting the real create/redirect/stats
 * endpoints with malformed JSON and unknown codes, plus a dedicated
 * test-only endpoint to force an unexpected 500, asserting the full
 * structured {@link ErrorResponse} JSON body and status code in each case.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Import(GlobalExceptionHandlerIntegrationTest.FailingEndpointConfig.class)
class GlobalExceptionHandlerIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void createEndpointMalformedJsonReturns400StructuredBody() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{not valid json"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.status", equalTo(400)))
                .andExpect(jsonPath("$.error", not(equalTo(""))))
                .andExpect(jsonPath("$.message", not(equalTo(""))))
                .andExpect(jsonPath("$.path", equalTo("/api/shorten")))
                .andExpect(jsonPath("$.timestamp").exists());
    }

    @Test
    void redirectEndpointUnknownCodeReturns404StructuredBody() throws Exception {
        mockMvc.perform(get("/{code}", "doesnotexist"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.status", equalTo(404)))
                .andExpect(jsonPath("$.error", not(equalTo(""))))
                .andExpect(jsonPath("$.message", not(equalTo(""))))
                .andExpect(jsonPath("$.path", equalTo("/doesnotexist")))
                .andExpect(jsonPath("$.timestamp").exists());
    }

    @Test
    void statsEndpointUnknownCodeReturns404StructuredBody() throws Exception {
        mockMvc.perform(get("/api/stats/{code}", "doesnotexist"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.status", equalTo(404)))
                .andExpect(jsonPath("$.error", not(equalTo(""))))
                .andExpect(jsonPath("$.message", not(equalTo(""))))
                .andExpect(jsonPath("$.path", equalTo("/api/stats/doesnotexist")))
                .andExpect(jsonPath("$.timestamp").exists());
    }

    @Test
    void createEndpointInvalidUrlReturns400StructuredBody() throws Exception {
        mockMvc.perform(post("/api/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("url", "not a url"))))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.status", equalTo(400)))
                .andExpect(jsonPath("$.path", equalTo("/api/shorten")));
    }

    @Test
    void forcedUnexpectedExceptionReturns500WithGenericNonLeakingBody() throws Exception {
        mockMvc.perform(get("/test-support/boom"))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.status", equalTo(500)))
                .andExpect(jsonPath("$.error", not(equalTo(""))))
                .andExpect(jsonPath("$.message", not(equalTo(""))))
                .andExpect(jsonPath("$.path", equalTo("/test-support/boom")))
                .andExpect(jsonPath("$.timestamp").exists())
                .andExpect(result -> {
                    String body = result.getResponse().getContentAsString();
                    org.assertj.core.api.Assertions.assertThat(body)
                            .doesNotContain("IllegalStateException")
                            .doesNotContain("boom-secret-detail")
                            .doesNotContain("at com.factory.app");
                });
    }

    /**
     * Test-only controller that always throws, used solely to exercise the
     * catch-all {@code Exception.class} handler through a real HTTP round
     * trip without modifying any production controller.
     */
    @TestConfiguration
    static class FailingEndpointConfig {

        @RestController
        static class FailingController {
            @GetMapping("/test-support/boom")
            public String boom() {
                throw new IllegalStateException("boom-secret-detail");
            }
        }

        @org.springframework.context.annotation.Bean
        FailingController failingController() {
            return new FailingController();
        }
    }
}
