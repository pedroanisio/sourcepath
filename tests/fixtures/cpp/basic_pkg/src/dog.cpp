#include "dog.h"

#include <utility>

namespace acme {

Dog::Dog(std::string name) : name_(std::move(name)) {}

std::string Dog::speak() const {
    Sound s("woof");
    Sound louder = s.amplify();
    return name_ + " says " + louder.text();
}

std::string Dog::name() const { return name_; }

int Dog::bumpAge(int current, int delta) {
    return current + delta;
}

}  // namespace acme
