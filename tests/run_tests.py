#!/usr/bin/env python3
import pytest
import sys
import os

if __name__ == "__main__":
    # Add the parent directory to the path so we can import the package
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    
    # Run all tests
    pytest.main(['-v', os.path.dirname(__file__)])
