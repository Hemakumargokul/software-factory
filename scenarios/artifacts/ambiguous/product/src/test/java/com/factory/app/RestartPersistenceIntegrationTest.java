package com.factory.app;

import static org.assertj.core.api.Assertions.assertThat;

import com.factory.app.config.DataDirectorySupport;
import com.factory.app.web.ShortenResponse;
import com.factory.app.web.StatsResponse;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.boot.context.event.ApplicationEnvironmentPreparedEvent;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.context.ApplicationListener;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

/**
 * End-to-end proof that FR7/FR8 hold across a real process-restart analogue:
 * a short URL and its click stats, created and recorded against one running
 * {@link ConfigurableApplicationContext}, remain retrievable from a brand new
 * context started later against the same on-disk, file-based H2 database.
 *
 * <p>Rather than relying on the shared {@code src/test/resources/application.properties}
 * (which intentionally points at a random-UUID in-memory DB per JVM to keep
 * test runs isolated from each other and from the running application), this
 * test builds two independent {@link SpringApplicationBuilder} contexts of
 * its own, both pointed at the identical {@code jdbc:h2:file:} URL rooted in
 * a JUnit {@link TempDir} that is discarded after the test. This proves the
 * production file-based persistence configuration itself (ddl-auto=update,
 * file: URL, directory auto-creation) without ever touching the real
 * {@code ./data} directory or leaking state into other test runs.</p>
 */
class RestartPersistenceIntegrationTest {

    @TempDir
    Path tempDir;

    private ConfigurableApplicationContext context;

    @AfterEach
    void tearDown() {
        if (context != null && context.isActive()) {
            context.close();
        }
    }

    private ConfigurableApplicationContext startContext() {
        // Nested under a subdirectory that does not exist yet on the first
        // call, exercising the same directory-creation path
        // (DataDirectorySupport) that the real application relies on for its
        // ./data directory.
        String dbUrl = "jdbc:h2:file:" + tempDir.resolve("nested").resolve("urlshortener") + ";AUTO_SERVER=FALSE";
        SpringApplicationBuilder builder = new SpringApplicationBuilder(AppApplication.class)
                .properties(Map.of(
                        "server.port", "0",
                        "spring.datasource.url", dbUrl,
                        "spring.datasource.driver-class-name", "org.h2.Driver",
                        "spring.datasource.username", "sa",
                        "spring.datasource.password", "",
                        "spring.jpa.hibernate.ddl-auto", "update",
                        "spring.jpa.database-platform", "org.hibernate.dialect.H2Dialect"
                ))
                .listeners((ApplicationListener<ApplicationEnvironmentPreparedEvent>) event ->
                        DataDirectorySupport.ensureDirectoryExists(
                                event.getEnvironment().getProperty("spring.datasource.url")));
        return builder.run();
    }

    private int portOf(ConfigurableApplicationContext ctx) {
        return Integer.parseInt(ctx.getEnvironment().getProperty("local.server.port"));
    }

    @Test
    void mappingAndClickCountSurviveContextRestartAgainstSameFileBasedDatabase() {
        context = startContext();
        TestRestTemplate rest = new TestRestTemplate();
        int firstPort = portOf(context);
        String baseUrl = "http://localhost:" + firstPort;

        String originalUrl = "https://example.com/restart-persistence-target";

        ResponseEntity<ShortenResponse> createResponse = rest.postForEntity(
                baseUrl + "/api/shorten", Map.of("url", originalUrl), ShortenResponse.class);
        assertThat(createResponse.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        String code = createResponse.getBody().code();
        assertThat(code).isNotBlank();

        // Record two clicks via the redirect endpoint before "restarting".
        rest.getForEntity(baseUrl + "/" + code, Void.class);
        rest.getForEntity(baseUrl + "/" + code, Void.class);

        ResponseEntity<StatsResponse> statsBeforeRestart =
                rest.getForEntity(baseUrl + "/api/stats/" + code, StatsResponse.class);
        assertThat(statsBeforeRestart.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(statsBeforeRestart.getBody().clicks()).isEqualTo(2L);

        // Simulate an application restart: close this context entirely (as
        // if the JVM had shut down) and start a brand new one against the
        // exact same on-disk H2 file path.
        context.close();

        context = startContext();
        int secondPort = portOf(context);
        String restartedBaseUrl = "http://localhost:" + secondPort;

        ResponseEntity<StatsResponse> statsAfterRestart =
                rest.getForEntity(restartedBaseUrl + "/api/stats/" + code, StatsResponse.class);
        assertThat(statsAfterRestart.getStatusCode())
                .as("mapping created before restart must still be resolvable after restart")
                .isEqualTo(HttpStatus.OK);
        assertThat(statsAfterRestart.getBody().code()).isEqualTo(code);
        assertThat(statsAfterRestart.getBody().url()).isEqualTo(originalUrl);
        assertThat(statsAfterRestart.getBody().clicks())
                .as("click count recorded before restart must survive")
                .isEqualTo(2L);

        // The redirect endpoint must also still resolve the code and keep
        // incrementing the persisted counter after restart.
        ResponseEntity<Void> redirectAfterRestart =
                rest.getForEntity(restartedBaseUrl + "/" + code, Void.class);
        assertThat(redirectAfterRestart.getStatusCode()).isEqualTo(HttpStatus.FOUND);
        assertThat(redirectAfterRestart.getHeaders().getLocation().toString()).isEqualTo(originalUrl);

        ResponseEntity<StatsResponse> statsAfterAnotherClick =
                rest.getForEntity(restartedBaseUrl + "/api/stats/" + code, StatsResponse.class);
        assertThat(statsAfterAnotherClick.getBody().clicks()).isEqualTo(3L);
    }

    @Test
    void codeGenerationDoesNotCollideAfterRestart() {
        context = startContext();
        TestRestTemplate rest = new TestRestTemplate();
        String baseUrl = "http://localhost:" + portOf(context);

        // Create several mappings before "restart" so the persisted max id
        // (and therefore the seeded counter after restart) is comfortably
        // greater than zero.
        java.util.Set<String> codesBeforeRestart = new java.util.HashSet<>();
        for (int i = 0; i < 5; i++) {
            ResponseEntity<ShortenResponse> response = rest.postForEntity(
                    baseUrl + "/api/shorten",
                    Map.of("url", "https://example.com/restart-codegen-" + i),
                    ShortenResponse.class);
            assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
            codesBeforeRestart.add(response.getBody().code());
        }
        assertThat(codesBeforeRestart).hasSize(5);

        context.close();
        context = startContext();
        String restartedBaseUrl = "http://localhost:" + portOf(context);

        // After restart, CodeGenerator must have re-seeded its counter from
        // the persisted max id rather than resetting to zero: new codes
        // generated post-restart must not collide with any pre-restart code,
        // and each new mapping must be independently creatable and
        // resolvable.
        java.util.Set<String> codesAfterRestart = new java.util.HashSet<>();
        for (int i = 0; i < 5; i++) {
            ResponseEntity<ShortenResponse> response = rest.postForEntity(
                    restartedBaseUrl + "/api/shorten",
                    Map.of("url", "https://example.com/restart-codegen-after-" + i),
                    ShortenResponse.class);
            assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
            String code = response.getBody().code();
            assertThat(codesBeforeRestart)
                    .as("post-restart code '%s' must not collide with a pre-restart code", code)
                    .doesNotContain(code);
            codesAfterRestart.add(code);
        }
        assertThat(codesAfterRestart)
                .as("all post-restart codes must themselves be unique")
                .hasSize(5);

        // Sanity: pre-restart mappings are still resolvable after restart,
        // proving this is genuine shared persistent state, not two
        // independent in-memory databases that merely didn't collide by luck.
        for (String code : codesBeforeRestart) {
            ResponseEntity<StatsResponse> stats =
                    rest.getForEntity(restartedBaseUrl + "/api/stats/" + code, StatsResponse.class);
            assertThat(stats.getStatusCode()).isEqualTo(HttpStatus.OK);
        }
    }
}
