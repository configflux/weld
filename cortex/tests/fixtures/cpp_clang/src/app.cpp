#include "app.h"

namespace app {

void ItemStore::add(Item item) {
    items_.push_back(item);
}

const std::vector<Item>& ItemStore::items() const {
    return items_;
}

int Bar::baz() {
    return 42;
}

}  // namespace app
