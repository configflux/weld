#include "app.h"
#include <cassert>

int main() {
    app::ItemStore store;
    assert(store.items().empty());
    store.add({1, "test"});
    assert(store.items().size() == 1);
    return 0;
}
