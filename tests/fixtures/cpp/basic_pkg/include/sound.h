#pragma once

#include <string>

namespace acme {

class Sound {
public:
    explicit Sound(std::string text);
    std::string text() const;
    Sound amplify() const;

private:
    std::string text_;
};

}  // namespace acme
