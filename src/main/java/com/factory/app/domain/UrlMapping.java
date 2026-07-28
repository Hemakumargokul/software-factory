package com.factory.app.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import jakarta.persistence.Index;

/**
 * Persistent mapping between a generated short code and the original URL it
 * points to, along with the number of times the short code has been resolved
 * via a redirect.
 */
@Entity
@Table(
        name = "url_mapping",
        uniqueConstraints = {
                @UniqueConstraint(name = "uk_url_mapping_code", columnNames = "code"),
                @UniqueConstraint(name = "uk_url_mapping_original_url", columnNames = "original_url")
        },
        indexes = {
                @Index(name = "idx_url_mapping_code", columnList = "code"),
                @Index(name = "idx_url_mapping_original_url", columnList = "original_url")
        }
)
public class UrlMapping {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "code", nullable = false, unique = true, length = 7)
    private String code;

    /**
     * Exact original URL string as submitted by the client. Column is sized
     * generously and matched via case-sensitive, unnormalized string equality
     * for dedup purposes.
     */
    @Column(name = "original_url", nullable = false, unique = true, length = 2048)
    private String originalUrl;

    @Column(name = "click_count", nullable = false)
    private long clickCount;

    protected UrlMapping() {
        // JPA
    }

    public UrlMapping(String code, String originalUrl) {
        this.code = code;
        this.originalUrl = originalUrl;
        this.clickCount = 0L;
    }

    public Long getId() {
        return id;
    }

    public String getCode() {
        return code;
    }

    public void setCode(String code) {
        this.code = code;
    }

    public String getOriginalUrl() {
        return originalUrl;
    }

    public void setOriginalUrl(String originalUrl) {
        this.originalUrl = originalUrl;
    }

    public long getClickCount() {
        return clickCount;
    }

    public void setClickCount(long clickCount) {
        this.clickCount = clickCount;
    }

    public void incrementClickCount() {
        this.clickCount += 1;
    }
}
