#!/usr/bin/env python3
"""
Configuration Editor for installer-creator

This module provides a command-line entry point for launching the GUI configuration editor.
"""
import sys
import os
from pathlib import Path
from .config_editor_ui import main as gui_main

def run(config_file='build_config.yaml'):
    """Run the configuration editor GUI."""
    try:
        # Ensure config_file is an absolute path
        if not os.path.isabs(config_file):
            config_file = os.path.abspath(config_file)
        
        # Check if the config file exists or its directory exists
        config_path = Path(config_file)
        if not config_path.exists() and not config_path.parent.exists():
            print(f"Warning: Config file directory {config_path.parent} does not exist.")
            print(f"Creating directory {config_path.parent}...")
            config_path.parent.mkdir(parents=True, exist_ok=True)
        
        gui_main(config_file)
    except Exception as e:
        print(f"Error starting configuration editor: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    run()
