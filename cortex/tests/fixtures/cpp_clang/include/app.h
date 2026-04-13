#pragma once

#include <string>
#include <vector>

namespace app {

struct Item {
    int id;
    std::string name;
};

class ItemStore {
public:
    void add(Item item);
    const std::vector<Item>& items() const;

private:
    std::vector<Item> items_;
};

// Sibling type used for the qualified-call test (Bar::baz()).
struct Bar {
    static int baz();
};

}  // namespace app
