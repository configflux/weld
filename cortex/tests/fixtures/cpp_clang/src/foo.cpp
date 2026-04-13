#include "foo.h"

namespace app {

// Out-of-class method definition (qualified_identifier whole-capture).
void Foo::bar() {
    // Static method called via qualified_identifier.
    int n = Foo::baz(3);
    (void)n;
}

int Foo::baz(int x) {
    return x + 1;
}

int free_add(int a, int b) {
    // Free function with calls: identity<int> and Foo::baz qualified call.
    int v = identity(a);
    int w = Foo::baz(b);
    return v + w;
}

}  // namespace app
