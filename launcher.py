#!/usr/bin/env python3
"""
TalkTally GUI Launcher

This script serves as the entry point for PyInstaller, avoiding relative import issues.
"""

import sys
import os
from pathlib import Path

# Add the src directory to Python path
src_dir = Path(__file__).parent / "src"
if src_dir.exists():
    sys.path.insert(0, str(src_dir))

# Now we can import and run the GUI
try:
    from talktally.gui import main
    if __name__ == "__main__":
        main()
except ImportError as e:
    print(f"Error importing TalkTally GUI: {e}")
    sys.exit(1)