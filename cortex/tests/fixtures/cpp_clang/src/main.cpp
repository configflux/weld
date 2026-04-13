#include "app.h"
#include "foo.h"
#include <iostream>

int main() {
    app::ItemStore store;
    store.add({1, "first"});
    for (const auto& item : store.items()) {
        std::cout << item.id << ": " << item.name << "\n";
    }
    // Qualified static-method call (Bar::baz()).
    int answer = app::Bar::baz();
    // Free function and inline-header method via foo.h.
    int sum = app::free_add(answer, 1);
    app::Foo f;
    sum += f.inline_double(sum);
    std::cout << sum << "\n";
    return 0;
}
