#pragma once

#include <string>

namespace app {

// Header/impl pair for out-of-class method definition tests.
class Foo {
public:
    void bar();                           // defined out-of-class in foo.cpp
    static int baz(int x);                // static method, called qualified
    inline int inline_double(int x) {     // inline header function
        return x * 2;
    }
};

// Template function defined in the header.
template <typename T>
T identity(T value) {
    return value;
}

// Free function declared in header, defined in foo.cpp.
int free_add(int a, int b);

}  // namespace app
