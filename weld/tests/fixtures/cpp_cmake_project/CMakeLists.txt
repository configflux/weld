cmake_minimum_required(VERSION 3.20)
project(example_app VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(APP_SOURCES src/main.cpp src/runner.cpp)

# Commented-out dep must be ignored.
# find_package(absl REQUIRED)

find_package(fmt REQUIRED)
find_package(Boost COMPONENTS system thread REQUIRED)

add_library(alpha STATIC lib_alpha/alpha.cpp lib_alpha/alpha.hpp)
target_include_directories(alpha PUBLIC lib_alpha)

add_library(beta SHARED lib_beta/beta.cpp lib_beta/beta.hpp)
target_include_directories(beta PUBLIC lib_beta)
target_link_libraries(beta PRIVATE alpha)

add_executable(example ${APP_SOURCES})
target_include_directories(example PRIVATE include)
target_link_libraries(example PRIVATE alpha beta fmt)
target_compile_definitions(example PRIVATE EXAMPLE_VERSION="0.1.0")

# Generator-expression and glob entries must end up as unresolved_labels.
add_executable(diag src/diag.cpp $<TARGET_OBJECTS:beta>)
target_link_libraries(diag PRIVATE alpha)
