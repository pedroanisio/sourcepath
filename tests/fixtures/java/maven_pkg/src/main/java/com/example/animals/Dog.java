package com.example.animals;

import com.google.common.base.Strings;
import java.util.List;
import org.slf4j.Logger;
import static java.lang.Math.max;

public class Dog extends Animal implements Runnable {
    private final String name;

    public Dog(String name) {
        this.name = name;
    }

    public Dog() {
        this("anonymous");
    }

    @Override
    public String name() {
        return Strings.isNullOrEmpty(name) ? super.name() : name;
    }

    @Override
    public String speak() {
        Sound s = new Sound("woof");
        return name + " says " + s.amplify().text();
    }

    @Override
    public void run() {
        speak();
    }

    public int bumpAge(int current, int delta) {
        return max(0, current + delta);
    }

    static class Pup {
        public String label() {
            return "puppy";
        }
    }
}
