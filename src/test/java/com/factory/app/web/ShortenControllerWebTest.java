package com.factory.app.web;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.RequestEntity;
import org.springframework.http.ResponseEntity;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ShortenControllerWebTest {

    @LocalServerPort
    private int port;

    // TestRestTemplate does not follow redirects by default (no
    // HttpClientOption.ENABLE_REDIRECTS configured), so 302 responses from
    // GET /{code} are observable here rather than silently followed.
    @Autowired
    private TestRestTemplate restTemplate;

    private String baseUrl() {
        return "http://localhost:" + port;
    }

    @Test
    void shortenReturns201WithCodeAndUrl() {
        String url = "https://example.com/web-test-" + System.nanoTime();
        ResponseEntity<ShortenResponse> response = post(url);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().code()).hasSize(7);
        assertThat(response.getBody().url()).isEqualTo(url);
    }

    @Test
    void shortenTwiceReturns200SameCode() {
        String url = "https://example.com/web-dedup-" + System.nanoTime();
        ResponseEntity<ShortenResponse> first = post(url);
        ResponseEntity<ShortenResponse> second = post(url);

        assertThat(first.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        assertThat(second.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(second.getBody().code()).isEqualTo(first.getBody().code());
    }

    @Test
    void missingUrlReturns400WithErrorBody() {
        ResponseEntity<ErrorResponse> response = restTemplate.postForEntity(
                baseUrl() + "/api/shorten", new java.util.HashMap<String, Object>(), ErrorResponse.class);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(response.getBody().error()).isNotBlank();
    }

    @Test
    void invalidSchemeReturns400() {
        ResponseEntity<ErrorResponse> response = postForError("ftp://example.com/file");
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void redirectFollowsToOriginalUrlAndIncrementsClicks() {
        String url = "https://example.com/redirect-test-" + System.nanoTime();
        String code = post(url).getBody().code();

        RequestEntity<Void> request = RequestEntity.method(HttpMethod.GET, java.net.URI.create(baseUrl() + "/" + code))
                .build();
        ResponseEntity<Void> redirectResponse = restTemplate.exchange(request, Void.class);

        assertThat(redirectResponse.getStatusCode()).isEqualTo(HttpStatus.FOUND);
        assertThat(redirectResponse.getHeaders().getLocation()).isEqualTo(java.net.URI.create(url));

        ResponseEntity<StatsResponse> stats =
                restTemplate.getForEntity(baseUrl() + "/api/stats/" + code, StatsResponse.class);
        assertThat(stats.getBody().clicks()).isEqualTo(1L);
    }

    @Test
    void unknownCodeRedirectReturns404() {
        ResponseEntity<ErrorResponse> response =
                restTemplate.getForEntity(baseUrl() + "/nosuchcode", ErrorResponse.class);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void unknownCodeStatsReturns404() {
        ResponseEntity<ErrorResponse> response =
                restTemplate.getForEntity(baseUrl() + "/api/stats/nosuchcode", ErrorResponse.class);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    private ResponseEntity<ShortenResponse> post(String url) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(org.springframework.http.MediaType.APPLICATION_JSON);
        RequestEntity<java.util.Map<String, String>> request = RequestEntity.post(
                        java.net.URI.create(baseUrl() + "/api/shorten"))
                .headers(headers)
                .body(java.util.Map.of("url", url));
        return restTemplate.exchange(request, ShortenResponse.class);
    }

    private ResponseEntity<ErrorResponse> postForError(String url) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(org.springframework.http.MediaType.APPLICATION_JSON);
        RequestEntity<java.util.Map<String, String>> request = RequestEntity.post(
                        java.net.URI.create(baseUrl() + "/api/shorten"))
                .headers(headers)
                .body(java.util.Map.of("url", url));
        return restTemplate.exchange(request, ErrorResponse.class);
    }
}
