package com.example.animals;

public final class Sound {
    private final String text;

    public Sound(String text) {
        this.text = text;
    }

    public String text() {
        return text;
    }

    public Sound amplify() {
        return new Sound(text + "!");
    }
}
