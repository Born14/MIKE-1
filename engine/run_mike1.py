#!/usr/bin/env python
"""
Run MIKE-1 Engine

Simple script to start the monitoring engine.
"""

import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# Load environment from project root
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from mike1.engine import main

if __name__ == "__main__":
    main()
