#pragma once

#include "animal.h"
#include "sound.h"

namespace acme {

class Dog : public Animal {
public:
    explicit Dog(std::string name);
    std::string speak() const override;
    std::string name() const override;
    int bumpAge(int current, int delta);

private:
    std::string name_;
};

}  // namespace acme
