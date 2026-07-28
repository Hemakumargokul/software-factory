package com.factory.app.codegen;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.factory.app.domain.UrlMapping;
import com.factory.app.repository.UrlMappingRepository;
import java.util.HashSet;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Pattern;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class CodeGeneratorTest {

    private static final Pattern BASE62_7 = Pattern.compile("^[0-9A-Za-z]{7}$");

    private UrlMappingRepository repository;
    private CodeGenerator generator;

    @BeforeEach
    void setUp() {
        repository = mock(UrlMappingRepository.class);
        when(repository.findAll()).thenReturn(java.util.List.of());
        when(repository.findByCode(anyString())).thenReturn(Optional.empty());
        generator = new CodeGenerator(repository);
        generator.seedCounter();
    }

    @Test
    void generatesCodeOfLengthSeven() {
        String code = generator.nextCode();
        assertThat(code).hasSize(7);
    }

    @Test
    void generatesOnlyBase62Characters() {
        for (int i = 0; i < 50; i++) {
            String code = generator.nextCode();
            assertThat(BASE62_7.matcher(code).matches())
                    .as("code '%s' should match base62 fixed-length pattern", code)
                    .isTrue();
        }
    }

    @Test
    void generatesUniqueCodesAcrossManyGenerations() {
        Set<String> seen = new HashSet<>();
        int count = 5000;
        for (int i = 0; i < count; i++) {
            String code = generator.nextCode();
            assertThat(seen.add(code)).as("code '%s' should not repeat", code).isTrue();
        }
        assertThat(seen).hasSize(count);
    }

    @Test
    void seedsCounterFromMaxExistingId() {
        UrlMappingRepository seededRepo = mock(UrlMappingRepository.class);
        UrlMapping existing = new UrlMapping("aaaaaaa", "https://example.com/x");
        setId(existing, 100L);
        when(seededRepo.findAll()).thenReturn(java.util.List.of(existing));
        when(seededRepo.findByCode(anyString())).thenReturn(Optional.empty());

        CodeGenerator seededGenerator = new CodeGenerator(seededRepo);
        seededGenerator.seedCounter();

        String code = seededGenerator.nextCode();
        // counter seeded to 100, next value is 101 -> encode(101) should differ from encode(1)
        assertThat(code).isEqualTo(CodeGenerator.encode(101L));
    }

    @Test
    void retriesOnSimulatedCollisionAndEventuallyReturnsUnusedCode() {
        // Simulate that the first two candidate codes already exist, third is free.
        String first = CodeGenerator.encode(1L);
        String second = CodeGenerator.encode(2L);
        String third = CodeGenerator.encode(3L);

        when(repository.findByCode(first)).thenReturn(Optional.of(new UrlMapping(first, "https://example.com/1")));
        when(repository.findByCode(second)).thenReturn(Optional.of(new UrlMapping(second, "https://example.com/2")));
        when(repository.findByCode(third)).thenReturn(Optional.empty());

        String code = generator.nextCode();

        assertThat(code).isEqualTo(third);
        verify(repository).findByCode(first);
        verify(repository).findByCode(second);
        verify(repository).findByCode(third);
    }

    @Test
    void throwsWhenRetriesExhausted() {
        when(repository.findByCode(anyString()))
                .thenReturn(Optional.of(new UrlMapping("xxxxxxx", "https://example.com/collide")));

        assertThatThrownBy(() -> generator.nextCode())
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void encodeProducesFixedLengthPadding() {
        assertThat(CodeGenerator.encode(0L)).hasSize(7);
        assertThat(CodeGenerator.encode(1L)).hasSize(7);
        assertThat(CodeGenerator.encode(61L)).hasSize(7);
        assertThat(CodeGenerator.encode(62L)).hasSize(7);
    }

    private static void setId(UrlMapping mapping, long id) {
        try {
            var field = UrlMapping.class.getDeclaredField("id");
            field.setAccessible(true);
            field.set(mapping, id);
        } catch (ReflectiveOperationException e) {
            throw new RuntimeException(e);
        }
    }
}
