package com.factory.app.web;

/**
 * Request DTO for {@code POST /api/shorten}. {@code url} is intentionally
 * typed as {@link Object} so Jackson will bind it whatever JSON value the
 * client sends (string, number, object, null, missing) rather than failing
 * deserialization outright; the service layer performs the "is this actually
 * a string" validation so a non-string value can be reported as a normal 400
 * JSON error body instead of a generic Spring deserialization error.
 */
public class ShortenRequest {

    private Object url;

    public ShortenRequest() {
    }

    public Object getUrl() {
        return url;
    }

    public void setUrl(Object url) {
        this.url = url;
    }
}
