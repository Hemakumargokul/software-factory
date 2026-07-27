"""Black-box contract tests for the URL shortener.

The contract asserted here is the one the scenario goal pins verbatim
(scenarios/greenfield.sh); the design stage passes it to the agent, and
this suite is the independent exam:

  POST /api/shorten   {"url": <absolute http/https URL>}
      201  {"code": ..., "url": ...}     idempotent per URL
      400  for non-http(s) schemes and malformed URLs
  GET  /{code}        redirect (3xx + Location) to the original URL
      404  for unknown codes
  GET  /api/stats/{code}
      200  {"code", "url", "clicks"}     clicks counts redirects
      404  for unknown codes
  Rate limit: POST /api/shorten capped at 30 requests/second per client,
      429 beyond the cap.
"""

import uuid

REDIRECT_STATUSES = (301, 302, 307, 308)


def shorten(client, url: str):
    return client.post("/api/shorten", json={"url": url})


def unique_url(tag: str = "") -> str:
    return f"https://example.com/{tag}{uuid.uuid4().hex}"


class TestShorten:
    def test_shorten_returns_201_with_code(self, client):
        response = shorten(client, unique_url())
        assert response.status_code == 201, response.text
        body = response.json()
        assert isinstance(body.get("code"), str) and body["code"]
        assert "url" in body

    def test_same_url_twice_returns_same_code(self, client):
        url = unique_url("dup-")
        first = shorten(client, url)
        second = shorten(client, url)
        assert first.status_code == 201
        assert second.status_code in (200, 201)  # idempotent re-shorten
        assert first.json()["code"] == second.json()["code"]

    def test_distinct_urls_get_distinct_codes(self, client):
        codes = {shorten(client, unique_url()).json()["code"] for _ in range(5)}
        assert len(codes) == 5


class TestSchemeAllowlist:
    def test_javascript_scheme_rejected(self, client):
        assert shorten(client, "javascript:alert(1)").status_code == 400

    def test_data_scheme_rejected(self, client):
        response = shorten(client, "data:text/html;base64,PHNjcmlwdD4=")
        assert response.status_code == 400

    def test_ftp_scheme_rejected(self, client):
        assert shorten(client, "ftp://example.com/file").status_code == 400

    def test_malformed_url_rejected(self, client):
        assert shorten(client, "not a url at all").status_code == 400

    def test_missing_url_rejected(self, client):
        assert client.post("/api/shorten", json={}).status_code == 400


class TestRedirect:
    def test_redirects_to_original(self, client):
        url = unique_url("redir-")
        code = shorten(client, url).json()["code"]
        response = client.get(f"/{code}")
        assert response.status_code in REDIRECT_STATUSES, response.text
        assert response.headers["location"] == url

    def test_unknown_code_is_404(self, client):
        assert client.get("/definitely-not-a-code-xyz").status_code == 404


class TestStats:
    def test_clicks_count_redirects(self, client):
        url = unique_url("stats-")
        code = shorten(client, url).json()["code"]
        for _ in range(3):
            client.get(f"/{code}")

        response = client.get(f"/api/stats/{code}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["code"] == code
        assert body["url"] == url
        assert body["clicks"] == 3

    def test_unknown_code_stats_404(self, client):
        assert client.get("/api/stats/nope-xyz").status_code == 404


class TestRateLimit:
    def test_burst_beyond_cap_gets_429(self, client):
        """The cap is 30 shorten calls/second per client; a 60-request burst
        must trip it. Every response is either a valid 201/200 or a 429 —
        never an error page."""
        statuses = [
            shorten(client, unique_url(f"burst{i}-")).status_code
            for i in range(60)
        ]
        assert 429 in statuses, f"no 429 in burst: {sorted(set(statuses))}"
        assert set(statuses) <= {200, 201, 429}
