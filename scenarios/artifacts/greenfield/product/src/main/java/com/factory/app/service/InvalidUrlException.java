package com.factory.app.service;

/**
 * Thrown when a submitted URL fails validation (missing, not a string,
 * malformed syntax, or a disallowed scheme). Callers map this to HTTP 400.
 */
public class InvalidUrlException extends RuntimeException {

    public InvalidUrlException(String message) {
        super(message);
    }
}
