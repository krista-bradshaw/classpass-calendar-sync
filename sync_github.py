#!/usr/bin/env python3
"""
ClassPass → iCloud Calendar sync
Entry point.
"""

import sys
from src.sync import sync

if __name__ == "__main__":
    try:
        sync()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"Unhandled error: {e}")
        sys.exit(1)
