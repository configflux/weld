"""Utility functions used across the legacy codebase."""

import os

def get_config_path():
    return os.environ.get("CONFIG_PATH", "/etc/app/config.ini")

def parse_csv_line(line):
    return line.strip().split(",")
