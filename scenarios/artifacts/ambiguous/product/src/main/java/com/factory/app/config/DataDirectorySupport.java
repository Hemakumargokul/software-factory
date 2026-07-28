package com.factory.app.config;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Ensures the on-disk directory backing the file-based H2 datasource exists
 * before Spring attempts to open a connection to it.
 *
 * <p>H2 will usually create missing parent directories itself for a
 * {@code jdbc:h2:file:} URL, but relying on that implicitly is fragile (it
 * has historically differed across H2 versions/platforms). Creating the
 * directory explicitly and idempotently up front removes that ambiguity and
 * gives a clear, early failure if the path is not writable.
 */
public final class DataDirectorySupport {

    private static final Logger log = LoggerFactory.getLogger(DataDirectorySupport.class);

    private DataDirectorySupport() {
    }

    /**
     * Parses the directory portion out of a {@code spring.datasource.url}
     * value of the form {@code jdbc:h2:file:<path>[;OPTIONS]} and creates it
     * (and any missing parents) if it doesn't already exist. No-op for any
     * other datasource URL shape (e.g. {@code jdbc:h2:mem:...}), so this is
     * safe to call unconditionally regardless of active profile.
     */
    public static void ensureDirectoryExists(String datasourceUrl) {
        if (datasourceUrl == null) {
            return;
        }
        String prefix = "jdbc:h2:file:";
        if (!datasourceUrl.startsWith(prefix)) {
            return;
        }

        String rest = datasourceUrl.substring(prefix.length());
        // Strip any trailing H2 connection options (e.g. ";AUTO_SERVER=TRUE").
        int semicolon = rest.indexOf(';');
        String rawPath = semicolon >= 0 ? rest.substring(0, semicolon) : rest;
        if (rawPath.isBlank()) {
            return;
        }

        Path dbFile = Paths.get(rawPath).toAbsolutePath().normalize();
        Path directory = dbFile.getParent();
        if (directory == null) {
            return;
        }

        try {
            Files.createDirectories(directory);
        } catch (IOException e) {
            throw new UncheckedIOException(
                    "Unable to create H2 data directory at " + directory, e);
        }
        log.debug("Ensured H2 data directory exists: {}", directory);
    }
}
