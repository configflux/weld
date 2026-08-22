"""Directory-form ``exclude:`` coverage for the wave-3 strategies (bd 9gdq).

Third wave of the defect fixed for ``python_callgraph`` in bd 3abf and
for eleven more strategies in bd eerc. eerc's enumeration was built from
``_resolve_glob`` call sites, so it missed every strategy that resolved
its glob inline -- ``if "**" in pattern: sorted(root.glob(pattern))``
followed by a per-file ``should_skip``.

Why that leaks: ``should_skip`` delegates to ``matches_exclude``, which
tests the *file* path with no ancestor-directory check, so a
directory-form pattern (``pkg/tests``) never matches
``pkg/tests/foo.py`` and the whole subtree is read and emitted anyway.
Only :func:`weld.glob_match.walk_glob` -- which prunes matching
directories *during descent* -- gives the directory form its meaning.

Every strategy below was **measured** leaking all three forms before the
fix, via exactly these fixtures. Eight of them were worse than the issue
described: ``bazel``, ``dds_idl``, ``deploy_surface``, ``manifest``,
``ros2_interfaces``, ``ros2_launch``, ``ros2_package`` and
``ros2_topology`` called ``should_skip`` *without* ``root=``, so they
matched excludes by basename only and the ``pkg/tests/**`` subtree form
leaked for them too -- ``exclude:`` was dead config for anything but a
bare filename.

Sibling batteries: ``weld_strategy_exclude_directory_form_test`` (the
eerc strategies) and ``weld_strategy_exclude_flat_glob_test`` (the
single-directory strategies, whose contract is narrower). The six
assertions are shared via :mod:`weld.tests._exclude_form_harness`.
"""

from __future__ import annotations

import unittest

from weld.strategies import (
    bazel,
    cpp_buildsystem_detector,
    cpp_cmake,
    cpp_conan,
    cpp_vcpkg,
    dds_idl,
    deploy_surface,
    flask,
    manifest,
    ros2_cmake,
    ros2_interfaces,
    ros2_launch,
    ros2_package,
    ros2_topology,
    runtime_contract,
)
from weld.tests._exclude_form_harness import EXCLUDED_DIR as _EXCLUDED_DIR
from weld.tests._exclude_form_harness import Case, ExcludeFormBatteryMixin

# -- fixture bodies ----------------------------------------------------------
# Each body must be rich enough that the strategy really emits something:
# the harness's baseline control fails loudly on an inert fixture.


def bazel_build(tag: str) -> str:
    return f"""
    py_library(
        name = "{tag}",
        srcs = ["{tag}.py"],
    )
    """


def cmake(tag: str) -> str:
    return f"""
    cmake_minimum_required(VERSION 3.10)
    project({tag})
    add_library({tag} STATIC {tag}.cpp)
    """


def ros2_cmake_body(tag: str) -> str:
    return f"""
    cmake_minimum_required(VERSION 3.10)
    project({tag})
    find_package(ament_cmake REQUIRED)
    add_executable({tag}_node src/{tag}.cpp)
    ament_target_dependencies({tag}_node rclcpp)
    ament_package()
    """


def conanfile(tag: str) -> str:
    return f"""
    [requires]
    {tag}/1.0

    [generators]
    CMakeDeps
    """


def vcpkg(tag: str) -> str:
    return f"""
    {{
      "name": "{tag}",
      "version": "1.0.0",
      "dependencies": ["fmt"]
    }}
    """


def idl(tag: str) -> str:
    return f"""
    module {tag}_mod {{
      struct {tag}Msg {{
        long id;
        string name;
      }};
    }};
    """


def compose(tag: str) -> str:
    return f"""
    version: "3"
    services:
      {tag}:
        image: {tag}:latest
        ports:
          - "8080:8080"
    """


def flask_app(tag: str) -> str:
    return f"""
    from flask import Flask

    app = Flask(__name__)

    @app.route("/{tag}")
    def {tag}_view():
        return "ok"
    """


def package_json(tag: str) -> str:
    return f"""
    {{
      "name": "{tag}",
      "version": "1.0.0",
      "scripts": {{"build": "echo {tag}"}}
    }}
    """


def ros_msg(tag: str) -> str:
    return f"int32 {tag}_id\nstring {tag}_name\n"


def launch_py(tag: str) -> str:
    return f"""
    from launch import LaunchDescription
    from launch_ros.actions import Node


    def generate_launch_description():
        return LaunchDescription([
            Node(package="{tag}_pkg", executable="{tag}_node", name="{tag}"),
        ])
    """


def package_xml(tag: str) -> str:
    return f"""
    <?xml version="1.0"?>
    <package format="3">
      <name>{tag}</name>
      <version>1.0.0</version>
      <description>{tag}</description>
      <maintainer email="a@b.c">a</maintainer>
      <license>MIT</license>
      <depend>rclcpp</depend>
    </package>
    """


def rclpy_node(tag: str) -> str:
    return f"""
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String


    class {tag.capitalize()}Node(Node):
        def __init__(self):
            super().__init__("{tag}")
            self.pub = self.create_publisher(String, "/{tag}_topic", 10)
    """


def runtime_md(tag: str) -> str:
    return f"""
    # {tag}

    ## Runtime Summary

    | Field | Value |
    | --- | --- |
    | entrypoint | {tag} |
    """


_DROP = _EXCLUDED_DIR

CASES: tuple[Case, ...] = (
    Case(
        "bazel", bazel, "pkg/**/BUILD.bazel",
        "pkg/BUILD.bazel", bazel_build("zzkeep"),
        f"{_DROP}/BUILD.bazel", bazel_build("zzdrop"),
        keep_marker="zzkeep",
    ),
    # This detector keys off the filename, not the body, so its only
    # droppable evidence is the path itself.
    Case(
        "cpp_buildsystem_detector", cpp_buildsystem_detector,
        "pkg/**/CMakeLists.txt",
        "pkg/CMakeLists.txt", cmake("zzkeep"),
        f"{_DROP}/CMakeLists.txt", cmake("zzdrop"),
        keep_marker="pkg/cmakelists.txt", drop_markers=(_DROP,),
    ),
    Case(
        "cpp_cmake", cpp_cmake, "pkg/**/CMakeLists.txt",
        "pkg/CMakeLists.txt", cmake("zzkeep"),
        f"{_DROP}/CMakeLists.txt", cmake("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "cpp_conan", cpp_conan, "pkg/**/conanfile.txt",
        "pkg/conanfile.txt", conanfile("zzkeep"),
        f"{_DROP}/conanfile.txt", conanfile("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "cpp_vcpkg", cpp_vcpkg, "pkg/**/vcpkg.json",
        "pkg/vcpkg.json", vcpkg("zzkeep"),
        f"{_DROP}/vcpkg.json", vcpkg("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "dds_idl", dds_idl, "pkg/**/*.idl",
        "pkg/zzkeep.idl", idl("zzkeep"),
        f"{_DROP}/zzdrop.idl", idl("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "deploy_surface", deploy_surface, "pkg/**/docker-compose*.yml",
        "pkg/docker-compose.zzkeep.yml", compose("zzkeep"),
        f"{_DROP}/docker-compose.zzdrop.yml", compose("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "flask", flask, "pkg/**/*.py",
        "pkg/zzkeep.py", flask_app("zzkeep"),
        f"{_DROP}/zzdrop.py", flask_app("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "manifest", manifest, "pkg/**/package.json",
        "pkg/package.json", package_json("zzkeep"),
        f"{_DROP}/package.json", package_json("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "ros2_cmake", ros2_cmake, "pkg/**/CMakeLists.txt",
        "pkg/CMakeLists.txt", ros2_cmake_body("zzkeep"),
        f"{_DROP}/CMakeLists.txt", ros2_cmake_body("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "ros2_interfaces", ros2_interfaces, "pkg/**/*.msg",
        "pkg/Zzkeep.msg", ros_msg("zzkeep"),
        f"{_DROP}/Zzdrop.msg", ros_msg("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "ros2_launch", ros2_launch, "pkg/**/*.launch.py",
        "pkg/zzkeep.launch.py", launch_py("zzkeep"),
        f"{_DROP}/zzdrop.launch.py", launch_py("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "ros2_package", ros2_package, "pkg/**/package.xml",
        "pkg/package.xml", package_xml("zzkeep"),
        f"{_DROP}/package.xml", package_xml("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "ros2_topology", ros2_topology, "pkg/**/*.py",
        "pkg/zzkeep.py", rclpy_node("zzkeep"),
        f"{_DROP}/zzdrop.py", rclpy_node("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "runtime_contract", runtime_contract, "pkg/**/*.md",
        "pkg/zzkeep.md", runtime_md("zzkeep"),
        f"{_DROP}/zzdrop.md", runtime_md("zzdrop"),
        keep_marker="zzkeep",
    ),
)


class StrategyExcludeWave3Test(ExcludeFormBatteryMixin, unittest.TestCase):
    """``exclude:`` must prune subtrees for every recursive-glob strategy."""

    CASES = CASES


if __name__ == "__main__":
    unittest.main()
