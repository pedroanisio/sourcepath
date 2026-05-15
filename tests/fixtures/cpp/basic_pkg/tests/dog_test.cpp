#include "dog.h"

namespace acme {

// Minimal hand-rolled "test" — no GoogleTest dep needed for the fixture.
int main() {
    Dog d("Rex");
    auto s = d.speak();
    return s.empty() ? 1 : 0;
}

}  // namespace acme
