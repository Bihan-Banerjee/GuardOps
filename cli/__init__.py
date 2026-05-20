# This file is intentionally (almost) empty.
# Its only job is to tell Python: "the cli/ directory is a package,
# not just a folder." Without this file, `from cli.main import cli` would fail.

# We define the version here so it's importable from anywhere:
# from cli import __version__
__version__ = "0.7.0"