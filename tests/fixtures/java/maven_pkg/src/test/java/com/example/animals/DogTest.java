package com.example.animals;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertNotNull;

public class DogTest {
    @Test
    void dogSpeaks() {
        Dog d = new Dog("Rex");
        assertNotNull(d.speak());
    }
}
