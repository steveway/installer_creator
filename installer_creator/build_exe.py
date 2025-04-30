#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil
import yaml
import threading
import queue
from queue import Empty
import time
from pathlib import Path
from typing import Dict, Any
from .build_installer import build_wix_installer
import signal

# Import pywinpty for Windows
if sys.platform == 'win32':
    try:
        import winpty
    except ImportError:
        print("Warning: pywinpty not installed. Windows terminal handling may be limited.")
        winpty = None

# Global variable to store the active process for termination
active_process = None

# Signal handler for graceful termination
def signal_handler(sig, frame):
    print("\nReceived termination signal. Cleaning up...")
    if active_process is not None:
        try:
            if hasattr(active_process, 'close') and callable(getattr(active_process, 'close')):
                # For winpty processes
                print("Terminating winpty process...")
                active_process.terminate(force=True)
            elif hasattr(active_process, 'kill') and callable(getattr(active_process, 'kill')):
                # For processes with kill method
                print("Killing process...")
                active_process.kill()
            else:
                # For subprocess processes
                print("Terminating subprocess...")
                active_process.terminate()
            print("Process terminated.")
        except Exception as e:
            print(f"Error terminating process: {e}")
    sys.exit(1)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
if sys.platform == 'win32':
    signal.signal(signal.SIGBREAK, signal_handler)
else:
    signal.signal(signal.SIGTERM, signal_handler)

def find_venv_python() -> str:
    """Find the Python interpreter in the virtual environment."""
    if os.environ.get('VIRTUAL_ENV'):
        if sys.platform == "win32":
            return os.path.join(os.environ['VIRTUAL_ENV'], "Scripts", "python.exe")
        else:
            return os.path.join(os.environ['VIRTUAL_ENV'], "bin", "python")
            
    venv_dirs = ['.venv', 'venv', 'env']
    python_exe = "python.exe" if sys.platform == "win32" else "python"
    python_path = os.path.join("Scripts" if sys.platform == "win32" else "bin", python_exe)
    
    current_dir = Path.cwd()
    for venv_dir in venv_dirs:
        # Check current directory
        venv_python = current_dir / venv_dir / python_path
        if venv_python.exists():
            return str(venv_python)
        
        # Check parent directory
        parent_venv_python = current_dir.parent / venv_dir / python_path
        if parent_venv_python.exists():
            return str(parent_venv_python)
    
    return sys.executable

def load_config(config_file: str = 'build_config.yaml') -> Dict[str, Any]:
    """Load and validate the build configuration."""
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
    
    required_keys = ['project', 'build']
    for key in required_keys:
        if key not in config:
            raise ValueError(f"Missing required key '{key}' in config file")
    
    return config

def build_nuitka_command(config: Dict[str, Any], python_exe: str) -> list:
    """Construct the Nuitka command from configuration."""
    project = config['project']
    build = config['build']
    main_file = project['main_file']
    
    # Get the project directory (where the config file is located)
    project_dir = os.path.dirname(os.path.abspath(config.get('_config_file_path', 'build_config.yaml')))
    
    # Convert main_file to absolute path if it's not already
    if not os.path.isabs(main_file):
        main_file = os.path.join(project_dir, main_file)
    
    cmd = [
        python_exe,
        "-m",
        "nuitka",
        main_file,
    ]

    # Basic options
    if build['options']['standalone']:
        cmd.append("--standalone")
    if build['options']['onefile']:
        cmd.append("--onefile")

    for extra_parameter in build['options'].get('extra_parameters', []):
        cmd.append(extra_parameter)

    # Project metadata
    if project.get('icon'):
        icon_path = project['icon']
        if not os.path.isabs(icon_path):
            icon_path = os.path.join(project_dir, icon_path)
        icon_path = os.path.normpath(icon_path)
        cmd.append(f"--windows-icon-from-ico={icon_path}")
    cmd.extend([
        f"--company-name={project['company']}",
        f"--product-name={project['name']}",
        f"--product-version={project['version']}",
        f"--file-description={project['description']}"
    ])

    # Splash screen
    if build['options'].get('splash_screen') and sys.platform == "win32":
        splash_path = build['options']['splash_screen']
        if not os.path.isabs(splash_path):
            splash_path = os.path.join(project_dir, splash_path)
        splash_path = os.path.normpath(splash_path)
        cmd.append(f"--onefile-windows-splash-screen-image={splash_path}")

    # Distribution metadata
    for meta in build['include'].get('distribution_metadata', []):
        cmd.append(f"--include-distribution-metadata={meta}")

    # Packages
    for package in build['include'].get('packages', []):
        cmd.append(f"--include-package={package}")
        if package == "PySide6":
            cmd.append("--enable-plugin=pyside6")

    for plugin in build['include'].get('plugins', []):
        cmd.append(f"--enable-plugin={plugin}")

    # Data directories
    for data_dir in build['include'].get('data_dirs', []):
        source_path = data_dir['source']
        target_path = data_dir['target']
        
        # Convert source_path to absolute path if it's not already
        if not os.path.isabs(source_path):
            source_path = os.path.join(project_dir, source_path)
        
        source_path = os.path.normpath(source_path)
        cmd.append(f"--include-data-dir={source_path}={target_path}")

    # External data
    for data in build['include'].get('external_data', []):
        cmd.append(f"--include-onefile-external-data={data}")

    if build['options'].get('remove_output', False):
        cmd.append("--remove-output")

    # External files
    for file in build['include'].get('files', []):
        file_path = file
        
        # Convert file_path to absolute path if it's not already
        if not os.path.isabs(file_path):
            abs_file_path = os.path.join(project_dir, file_path)
            abs_file_path = os.path.normpath(abs_file_path)
            cmd.append(f"--include-data-file={abs_file_path}={file_path}")
        else:
            file_path = os.path.normpath(file_path)
            cmd.append(f"--include-data-file={file_path}={os.path.basename(file_path)}")

    # Remove .exe from output filename if we are not on Windows
    if sys.platform == "win32":
        file_name = build['output']['filename']
    else:
        file_name = build['output']['filename'][:-4]
        
    # Output directory - convert to absolute path if it's not already
    output_dir = build['output']['directory']
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(project_dir, output_dir)
    
    output_dir = os.path.normpath(output_dir)
        
    # Output settings
    cmd.extend([
        f"--output-dir={output_dir}",
        f"--output-filename={file_name}"
    ])

    # Debug settings
    debug = config.get('debug', {})
    if debug.get('enabled', False):
        cmd.extend([
            "--windows-console-mode=force",
            "--force-stdout-spec={PROGRAM_BASE}.out.txt",
            "--force-stderr-spec={PROGRAM_BASE}.err.txt"
        ])
    else:
        cmd.append("--windows-console-mode=disable")

    return cmd

def main(config_file='build_config.yaml'):
    """Build executable with Nuitka
    
    Args:
        config_file: Path to configuration YAML file
    """
    global active_process
    
    try:
        # Ensure config_file is an absolute path
        if not os.path.isabs(config_file):
            config_file = os.path.abspath(config_file)
            
        print(f"Using config file: {config_file}")
        sys.stdout.flush()
        
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
            
        # Store the config file path in the config for reference
        config['_config_file_path'] = config_file
        
        # Find virtual environment Python
        python_exe = find_venv_python()
        print(f"Using Python executable: {python_exe}")
        sys.stdout.flush()
        
        # Verify Python executable exists
        if not os.path.exists(python_exe):
            raise FileNotFoundError(f"Python executable not found: {python_exe}")
        
        # Get build configuration
        build_config = config.get('build', {})
        
        # Get output directory
        output_dir_path = Path(build_config.get('output', {}).get('directory', 'dist'))
        
        # Convert to absolute path if it's relative
        if not output_dir_path.is_absolute():
            project_dir = os.path.dirname(config_file)
            output_dir_path = Path(project_dir) / output_dir_path
            
        print(f"Output directory: {output_dir_path}")
        sys.stdout.flush()
        
        # Create output directory if it doesn't exist
        try:
            output_dir_path.mkdir(parents=True, exist_ok=True)
            print(f"Ensured output directory exists: {output_dir_path}")
            sys.stdout.flush()
        except PermissionError as pe:
            print(f"Permission error creating output directory: {pe}")
            print("Try running as administrator or changing the output directory.")
            sys.exit(1)
        except Exception as e:
            print(f"Error creating output directory: {e}")
            sys.exit(1)
        
        # Check for any running processes that might be locking files
        if sys.platform == "win32":
            try:
                import psutil
                
                # Get the output executable path
                output_filename = build_config.get('output', {}).get('filename', '')
                if not output_filename:
                    # Use main file name as fallback
                    main_file = config.get('project', {}).get('main_file', '')
                    if main_file:
                        output_filename = os.path.basename(main_file)
                        if output_filename.endswith('.py'):
                            output_filename = output_filename[:-3]
                    else:
                        output_filename = 'output'
                
                # Ensure it has .exe extension on Windows
                output_exe = output_dir_path / output_filename
                if not output_exe.suffix:
                    output_exe = output_exe.with_suffix('.exe')
                    
                print(f"Checking if output executable is locked: {output_exe}")
                sys.stdout.flush()
                
                if output_exe.exists():
                    for proc in psutil.process_iter(['pid', 'name']):
                        try:
                            for file in proc.open_files():
                                if file.path.lower() == str(output_exe).lower():
                                    print(f"Warning: Process {proc.name()} (PID: {proc.pid}) is locking the output file.")
                                    print(f"Attempting to terminate the process...")
                                    proc.terminate()
                                    print(f"Process terminated.")
                        except (psutil.AccessDenied, psutil.NoSuchProcess):
                            pass
            except ImportError:
                print("psutil not installed, skipping process check")
        
        # Build the Nuitka command
        cmd = build_nuitka_command(config, python_exe)
        
        print("Building executable with Nuitka...")
        sys.stdout.flush()
        print(f"Command: {' '.join(cmd)}")
        sys.stdout.flush()
        
        # Send initial progress update
        print("PROGRESS:10:Starting build process...")
        sys.stdout.flush()
        
        try:
            # On Windows, use pywinpty if available
            if sys.platform == 'win32' and 'winpty' in sys.modules and winpty is not None:
                # Use winpty for Windows terminal handling
                print("Using winpty for Windows terminal handling...")
                sys.stdout.flush()
                
                # Convert command list to a single string for display
                cmd_str = ' '.join(cmd)
                print(f"Executing command: {cmd_str}")
                sys.stdout.flush()
                
                # Create a winpty terminal
                term = winpty.PtyProcess.spawn(cmd, cwd=os.path.dirname(config_file), env=os.environ)
                active_process = term
                
                # Process output in real-time
                line_count = 0
                last_progress = 10
                
                # Read output in a loop
                last_progress_time = time.time()
                while True:
                    try:
                        # Read output
                        output = term.read()
                        if output:
                            # Process each line
                            for line in output.splitlines(True):  # Keep line endings
                                # Print the line to stdout
                                print(line, end='')
                                sys.stdout.flush()
                                line_count += 1
                                
                                # Add progress markers based on content
                                progress_updated = False
                                
                                # Check for specific Nuitka output patterns
                                if "Nuitka-Options:" in line:
                                    print("PROGRESS:30:Configuring Nuitka options...")
                                    sys.stdout.flush()
                                    progress_updated = True
                                elif "Nuitka:INFO:" in line and "Starting Python compilation" in line:
                                    print("PROGRESS:35:Starting Python compilation...")
                                    sys.stdout.flush()
                                    progress_updated = True
                                elif "Nuitka:INFO:" in line and "C compiler" in line:
                                    print("PROGRESS:40:Setting up C compiler...")
                                    sys.stdout.flush()
                                    progress_updated = True
                                elif "Compiling" in line:
                                    print("PROGRESS:45:Compiling Python modules...")
                                    sys.stdout.flush()
                                    progress_updated = True
                                elif "Generating" in line:
                                    print("PROGRESS:50:Generating C code...")
                                    sys.stdout.flush()
                                    progress_updated = True
                                elif "Building extension modules" in line:
                                    print("PROGRESS:55:Building extension modules...")
                                    sys.stdout.flush()
                                    progress_updated = True
                                elif "Linking" in line:
                                    print("PROGRESS:60:Linking modules...")
                                    sys.stdout.flush()
                                    progress_updated = True
                                elif "Packaging" in line:
                                    print("PROGRESS:75:Packaging application...")
                                    sys.stdout.flush()
                                    progress_updated = True
                                elif "Copying" in line:
                                    print("PROGRESS:85:Copying dependencies...")
                                    sys.stdout.flush()
                                    progress_updated = True
                                elif "Executable built successfully" in line:
                                    print("PROGRESS:95:Build completed successfully!")
                                    sys.stdout.flush()
                                    progress_updated = True
                                
                                # Add periodic progress updates for long-running stages
                                current_time = time.time()
                                if not progress_updated and (current_time - last_progress_time) > 5:  # Every 5 seconds
                                    # Slowly increase progress up to 90% based on line count
                                    new_progress = min(90, 30 + (line_count // 20))
                                    if new_progress > last_progress:
                                        print(f"PROGRESS:{new_progress}:Building... (Line {line_count})")
                                        sys.stdout.flush()
                                        last_progress = new_progress
                                        last_progress_time = current_time
                            
                        # Check if process is still running
                        if not term.isalive():
                            break
                                
                    except Exception as e:
                        print(f"Error reading output: {e}")
                        sys.stdout.flush()
                        break
                    
                # Wait for process to finish
                return_code = term.wait()
                active_process = None
            else:
                # Use shell=True on Windows to help with permissions
                use_shell = sys.platform == 'win32'
                
                # Run the command with real-time output
                if use_shell:
                    # Properly quote arguments with spaces
                    cmd_str = ' '.join(f'"{arg}"' if ' ' in arg or '\\' in arg else arg for arg in cmd)
                    print(f"Executing command: {cmd_str}")
                    sys.stdout.flush()
                    
                    # Use Popen to capture output in real-time
                    process = subprocess.Popen(
                        cmd_str,
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        universal_newlines=True,
                        cwd=os.path.dirname(config_file),
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0
                    )
                else:
                    # Use Popen to capture output in real-time
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        universal_newlines=True,
                        cwd=os.path.dirname(config_file),
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0
                    )
                
                active_process = process
                
                # Process output in real-time
                line_count = 0
                last_progress = 10
                last_progress_time = time.time()
                
                for line in iter(process.stdout.readline, ''):
                    # Print the line to stdout
                    print(line, end='')
                    sys.stdout.flush()
                    line_count += 1
                    
                    # Add progress markers based on content
                    progress_updated = False
                    
                    # Check for specific Nuitka output patterns
                    if "Nuitka-Options:" in line:
                        print("PROGRESS:30:Configuring Nuitka options...")
                        sys.stdout.flush()
                        progress_updated = True
                    elif "Nuitka:INFO:" in line and "Starting Python compilation" in line:
                        print("PROGRESS:35:Starting Python compilation...")
                        sys.stdout.flush()
                        progress_updated = True
                    elif "Nuitka:INFO:" in line and "C compiler" in line:
                        print("PROGRESS:40:Setting up C compiler...")
                        sys.stdout.flush()
                        progress_updated = True
                    elif "Compiling" in line:
                        print("PROGRESS:45:Compiling Python modules...")
                        sys.stdout.flush()
                        progress_updated = True
                    elif "Generating" in line:
                        print("PROGRESS:50:Generating C code...")
                        sys.stdout.flush()
                        progress_updated = True
                    elif "Building extension modules" in line:
                        print("PROGRESS:55:Building extension modules...")
                        sys.stdout.flush()
                        progress_updated = True
                    elif "Linking" in line:
                        print("PROGRESS:60:Linking modules...")
                        sys.stdout.flush()
                        progress_updated = True
                    elif "Packaging" in line:
                        print("PROGRESS:75:Packaging application...")
                        sys.stdout.flush()
                        progress_updated = True
                    elif "Copying" in line:
                        print("PROGRESS:85:Copying dependencies...")
                        sys.stdout.flush()
                        progress_updated = True
                    elif "Executable built successfully" in line:
                        print("PROGRESS:95:Build completed successfully!")
                        sys.stdout.flush()
                        progress_updated = True
                    
                    # Add periodic progress updates for long-running stages
                    current_time = time.time()
                    if not progress_updated and (current_time - last_progress_time) > 5:  # Every 5 seconds
                        # Slowly increase progress up to 90% based on line count
                        new_progress = min(90, 30 + (line_count // 20))
                        if new_progress > last_progress:
                            print(f"PROGRESS:{new_progress}:Building... (Line {line_count})")
                            sys.stdout.flush()
                            last_progress = new_progress
                            last_progress_time = current_time
                
                # Wait for process to finish
                return_code = process.wait()
                active_process = None
            
            # Check return code
            if return_code != 0:
                print(f"Nuitka build failed with return code {return_code}")
                sys.stdout.flush()
                sys.exit(return_code)
            else:
                print("PROGRESS:95:Finalizing build...")
                sys.stdout.flush()
                print("Executable built successfully!")
                sys.stdout.flush()
                print("PROGRESS:100:Build completed successfully!")
                sys.stdout.flush()
        except Exception as e:
            print(f"Error running Nuitka: {e}")
            import traceback
            print(f"Exception details: {traceback.format_exc()}")
            sys.exit(1)

        # --- Copy files/folders beside the executable ---
        copy_beside_items = build_config.get('copy_beside', [])
        if copy_beside_items:
            print(f"\nCopying additional items to {output_dir_path}...")
            sys.stdout.flush()
            
            # Get the project directory (where the config file is located)
            project_dir = os.path.dirname(config_file)
            
            for item_name in copy_beside_items:
                source_path = Path(project_dir) / item_name
                dest_path = output_dir_path / item_name
                
                if not source_path.exists():
                    print(f"Warning: Source item '{item_name}' not found at '{source_path}', skipping.")
                    sys.stdout.flush()
                    continue
                
                try:
                    if source_path.is_dir():
                        print(f"  Copying directory: {item_name}")
                        sys.stdout.flush()
                        shutil.copytree(source_path, dest_path, dirs_exist_ok=True)
                    elif source_path.is_file():
                        print(f"  Copying file: {item_name}")
                        sys.stdout.flush()
                        # Ensure the destination directory exists (should already exist)
                        output_dir_path.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source_path, dest_path)
                    else:
                        print(f"Warning: Source item '{item_name}' is not a file or directory, skipping.")
                        sys.stdout.flush()
                except PermissionError as pe:
                    print(f"Permission error copying '{item_name}': {pe}")
                    print("Try running as administrator or changing the output directory.")
                    sys.stdout.flush()
                except Exception as copy_e:
                    print(f"Error copying '{item_name}': {copy_e}")
                    sys.stdout.flush()
            print("Finished copying additional items.")
            sys.stdout.flush()
            print("PROGRESS:100:Build completed successfully!")
            sys.stdout.flush()
        # --- End copy section ---

        
        # Build installer if enabled
        if config.get('installer', {}).get('enabled', False):
            print("\nBuilding WiX installer...")
            sys.stdout.flush()
            build_wix_installer(config)
    
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        print(f"Exception details: {traceback.format_exc()}")
        sys.exit(1)

if __name__ == "__main__":
    # Maintain backward compatibility when run directly
    main()
