package com.factory.app.web;

/**
 * Uniform JSON error body shape used by all error responses:
 * {"error": "<message>"}.
 */
public record ErrorResponse(String error) {
}
