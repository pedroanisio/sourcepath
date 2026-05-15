#pragma once

#include <string>

namespace acme {

class Animal {
public:
    virtual ~Animal() = default;
    virtual std::string speak() const = 0;
    virtual std::string name() const;
};

}  // namespace acme
