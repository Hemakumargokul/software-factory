package com.factory.app.service;

/**
 * Thrown when a lookup by short code finds no matching mapping. Callers map
 * this to HTTP 404.
 */
public class CodeNotFoundException extends RuntimeException {

    public CodeNotFoundException(String code) {
        super("No mapping found for code '" + code + "'");
    }
}
