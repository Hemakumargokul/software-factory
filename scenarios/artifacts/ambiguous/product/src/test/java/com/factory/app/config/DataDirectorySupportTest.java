package com.factory.app.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class DataDirectorySupportTest {

    @TempDir
    Path tempDir;

    @Test
    void createsMissingNestedDirectoryForFileUrl() {
        Path dbFile = tempDir.resolve("a").resolve("b").resolve("urlshortener");
        assertThat(Files.exists(dbFile.getParent())).isFalse();

        DataDirectorySupport.ensureDirectoryExists("jdbc:h2:file:" + dbFile + ";AUTO_SERVER=TRUE");

        assertThat(Files.isDirectory(dbFile.getParent())).isTrue();
    }

    @Test
    void isIdempotentWhenDirectoryAlreadyExists() {
        Path dbFile = tempDir.resolve("urlshortener");

        assertThatCode(() -> {
            DataDirectorySupport.ensureDirectoryExists("jdbc:h2:file:" + dbFile);
            DataDirectorySupport.ensureDirectoryExists("jdbc:h2:file:" + dbFile);
        }).doesNotThrowAnyException();

        assertThat(Files.isDirectory(tempDir)).isTrue();
    }

    @Test
    void ignoresNonFileBasedUrlsSuchAsInMemory() {
        assertThatCode(() -> DataDirectorySupport.ensureDirectoryExists("jdbc:h2:mem:urlshortener;DB_CLOSE_DELAY=-1"))
                .doesNotThrowAnyException();
    }

    @Test
    void ignoresNullUrl() {
        assertThatCode(() -> DataDirectorySupport.ensureDirectoryExists(null)).doesNotThrowAnyException();
    }

    @Test
    void handlesUrlWithoutTrailingOptions() {
        Path dbFile = tempDir.resolve("plain").resolve("urlshortener");

        DataDirectorySupport.ensureDirectoryExists("jdbc:h2:file:" + dbFile);

        assertThat(Files.isDirectory(dbFile.getParent())).isTrue();
    }
}
