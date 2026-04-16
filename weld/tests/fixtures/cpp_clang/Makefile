.PHONY: build test clean

build:
	cmake -B build -DCMAKE_BUILD_TYPE=Release
	cmake --build build

test: build
	cd build && ctest --output-on-failure

clean:
	rm -rf build
