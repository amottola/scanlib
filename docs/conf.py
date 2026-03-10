"""Sphinx configuration for scanlib documentation."""

import sys
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

project = "scanlib"
copyright = "2026, Angelo Mottola"
author = "Angelo Mottola"
release = version("scanlib")

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx_autodoc_typehints",
]

autodoc_member_order = "bysource"
autodoc_typehints = "description"
always_use_bars_union = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

html_theme = "furo"

exclude_patterns = ["_build"]
