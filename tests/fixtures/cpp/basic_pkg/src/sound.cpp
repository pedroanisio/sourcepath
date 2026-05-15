#include "sound.h"

#include <utility>

namespace acme {

Sound::Sound(std::string text) : text_(std::move(text)) {}

std::string Sound::text() const { return text_; }

Sound Sound::amplify() const { return Sound(text_ + "!"); }

}  // namespace acme
