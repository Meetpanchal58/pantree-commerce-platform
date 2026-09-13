"""Compatibility validator. Use validate_dataset.py for the current warehouse."""
import runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).with_name('validate_dataset.py')), run_name='__main__')
