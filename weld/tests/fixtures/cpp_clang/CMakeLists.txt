cmake_minimum_required(VERSION 3.20)
project(example_cpp VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

add_executable(main src/main.cpp)
target_include_directories(main PRIVATE include)

enable_testing()
add_executable(test_app tests/test_main.cpp)
add_test(NAME test_app COMMAND test_app)
