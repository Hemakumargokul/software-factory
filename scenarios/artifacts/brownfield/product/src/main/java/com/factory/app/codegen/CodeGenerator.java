package com.factory.app.codegen;

import com.factory.app.repository.UrlMappingRepository;
import jakarta.annotation.PostConstruct;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.stereotype.Component;

/**
 * Generates 7-character base62 ([0-9A-Za-z]) short codes.
 *
 * <p>Strategy: an in-memory {@link AtomicLong} counter is seeded at startup
 * from the max existing primary key in the {@code url_mapping} table (so
 * restarts never reuse counter values already consumed), and each call to
 * {@link #nextCode()} atomically increments it and encodes the result to
 * base62, left-padded to a fixed length of 7 characters. Because the
 * underlying {@code code} column has a unique constraint, and because
 * multiple instances or clock skew could theoretically produce a collision
 * (e.g. after manual data manipulation), generation is wrapped in a bounded
 * retry loop that re-checks the repository for existence before accepting a
 * candidate code.</p>
 */
@Component
public class CodeGenerator {

    /** Fixed output length required by the API contract. */
    public static final int CODE_LENGTH = 7;

    private static final String ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
    private static final int BASE = ALPHABET.length();

    /**
     * Upper bound on collision-retry attempts before giving up with an
     * exception. With a 62^7 (~3.5 trillion) code space this is only ever
     * exercised under pathological collision conditions (e.g. in tests that
     * simulate collisions).
     */
    private static final int MAX_RETRIES = 1000;

    private final UrlMappingRepository repository;
    private final AtomicLong counter = new AtomicLong(0L);

    public CodeGenerator(UrlMappingRepository repository) {
        this.repository = repository;
    }

    @PostConstruct
    void seedCounter() {
        long maxId = repository.findAll().stream()
                .mapToLong(m -> m.getId() == null ? 0L : m.getId())
                .max()
                .orElse(0L);
        counter.set(maxId);
    }

    /**
     * Produces a short code guaranteed (barring exhaustion of
     * {@link #MAX_RETRIES} attempts) to not already exist in the repository
     * at the moment of the check. Callers must still handle a possible
     * {@code DataIntegrityViolationException} on save if a concurrent request
     * wins a race for the same code, and may retry by calling this method
     * again.
     */
    public String nextCode() {
        for (int attempt = 0; attempt < MAX_RETRIES; attempt++) {
            String candidate = encode(counter.incrementAndGet());
            if (repository.findByCode(candidate).isEmpty()) {
                return candidate;
            }
        }
        throw new IllegalStateException(
                "Unable to generate a unique short code after " + MAX_RETRIES + " attempts");
    }

    /**
     * Encodes a positive long as base62 and left-pads with the alphabet's
     * zero digit ('0') to {@link #CODE_LENGTH} characters.
     */
    static String encode(long value) {
        StringBuilder sb = new StringBuilder();
        long remaining = value;
        if (remaining == 0) {
            sb.append(ALPHABET.charAt(0));
        }
        while (remaining > 0) {
            int digit = (int) (remaining % BASE);
            sb.append(ALPHABET.charAt(digit));
            remaining /= BASE;
        }
        while (sb.length() < CODE_LENGTH) {
            sb.append(ALPHABET.charAt(0));
        }
        sb.reverse();
        // In the extremely unlikely event the counter grows beyond 7
        // base62 digits, truncate to the least-significant CODE_LENGTH
        // characters to keep the contract's fixed length invariant.
        if (sb.length() > CODE_LENGTH) {
            return sb.substring(sb.length() - CODE_LENGTH);
        }
        return sb.toString();
    }
}
