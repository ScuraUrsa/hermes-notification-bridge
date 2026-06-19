#!/usr/bin/env python3
"""Entry point to run the Notification Bridge server."""

import sys
import os

# Add parent to path so bridge package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.server import main

if __name__ == "__main__":
    main()
