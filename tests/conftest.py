"""Pytest-configuratie: repo-root op sys.path zodat 'import db' en
'import product_import' werken ongeacht vanuit welke map pytest draait, en zodat
'from _schildertool_extract import extract' binnen tests/ werkt."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
for _p in (REPO_ROOT, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
