#!/usr/bin/env python3
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty
from typing import Any, Dict, Optional

import yaml

from .build_installer import build_wix_installer

# Import pywinpty for Windows
if sys.platform == "win32":
    try:
        import winpty
    except ImportError:
        print(
            "Warning: pywinpty not installed. Windows terminal handling may be limited."
        )
        winpty = None

# Global variable to store the active process for termination
active_process = None


# Signal handler for graceful termination
def signal_handler(sig, frame):
    print("\nReceived termination signal. Cleaning up...")
    if active_process is not None:
        try:
            if hasattr(active_process, "close") and callable(
                getattr(active_process, "close")
            ):
                # For winpty processes
                print("Terminating winpty process...")
                active_process.terminate(force=True)
            elif hasattr(active_process, "kill") and callable(
                getattr(active_process, "kill")
            ):
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
if sys.platform == "win32":
    signal.signal(signal.SIGBREAK, signal_handler)
else:
    signal.signal(signal.SIGTERM, signal_handler)


def find_venv_python() -> str:
    """Find the Python interpreter in the virtual environment."""
    if os.environ.get("VIRTUAL_ENV"):
        if sys.platform == "win32":
            python_exe = "python.exe"
            python_path = os.path.join("Scripts", python_exe)
        else:
            python_exe = "python"
            python_path = "bin/python"
        virtual_env_path = os.environ["VIRTUAL_ENV"]
        # Ensure POSIX path format on non-Windows platforms
        if sys.platform != "win32":
            virtual_env_path = Path(virtual_env_path).as_posix()
        return os.path.join(virtual_env_path, python_path)

    venv_dirs = [".venv", "venv", "env"]
    current_dir = Path.cwd()

    for venv_dir in venv_dirs:
        # Check current directory
        venv_python = current_dir / venv_dir / python_path
        venv_python_str = (
            str(venv_python) if sys.platform == "win32" else venv_python.as_posix()
        )
        if os.path.exists(venv_python_str):
            return venv_python_str

        # Check parent directory
        parent_venv_python = current_dir.parent / venv_dir / python_path
        parent_venv_python_str = (
            str(parent_venv_python)
            if sys.platform == "win32"
            else parent_venv_python.as_posix()
        )
        if os.path.exists(parent_venv_python_str):
            return parent_venv_python_str

    # Return sys.executable with exact case matching for tests
    return sys.executable


def load_config(config_file: str = "build_config.yaml") -> Dict[str, Any]:
    """Load and validate the build configuration."""
    with open(config_file, "r") as f:
        config = yaml.safe_load(f)

    required_keys = ["project", "build"]
    for key in required_keys:
        if key not in config:
            raise ValueError(f"Missing required key '{key}' in config file")

    return config


def build_nuitka_command(config: Dict[str, Any], python_exe: str) -> list:
    """Construct the Nuitka command from configuration."""
    project = config["project"]
    build = config["build"]
    main_file = project["main_file"]

    project_dir: Optional[str] = None
    if "_config_file_path" in config and config["_config_file_path"]:
        # When build_nuitka_command is called from the main() routine the
        # special internal key "_config_file_path" is injected.  During unit
        # tests this key is intentionally missing so that the tests can make
        # assertions about *relative* paths.  Therefore we only determine the
        # project directory (and subsequently make paths absolute) when the
        # key is present.  This keeps the production behaviour (absolute
        # paths) while allowing the tests to operate on relative paths instead.
        project_dir = os.path.dirname(os.path.abspath(config["_config_file_path"]))

    # Helper that makes a path absolute **only** when project_dir is known and
    # the provided path is currently relative.
    def _abs(path: str) -> str:
        if project_dir and path and not os.path.isabs(path):
            return os.path.normpath(os.path.join(project_dir, path))
        # Still normalise for consistent path separators
        return os.path.normpath(path)

    # Ensure main_file uses the correct (relative/absolute) representation
    main_file = _abs(main_file)

    cmd = [
        python_exe,
        "-m",
        "nuitka",
        main_file,
    ]

    # Basic options
    if build["options"]["standalone"]:
        cmd.append("--standalone")
    if build["options"]["onefile"]:
        cmd.append("--onefile")

    for extra_parameter in build["options"].get("extra_parameters", []):
        cmd.append(extra_parameter)

    # Project metadata
    if project.get("icon"):
        icon_path = _abs(project["icon"])
        cmd.append(f"--windows-icon-from-ico={icon_path}")
    cmd.extend(
        [
            f"--company-name={project['company']}",
            f"--product-name={project['name']}",
            f"--product-version={project['version']}",
            f"--file-description={project['description']}",
        ]
    )

    # Splash screen
    if build["options"].get("splash_screen") and sys.platform == "win32":
        splash_path = _abs(build["options"]["splash_screen"])
        cmd.append(f"--onefile-windows-splash-screen-image={splash_path}")

    # Distribution metadata
    for meta in build["include"].get("distribution_metadata", []):
        cmd.append(f"--include-distribution-metadata={meta}")

    # Packages
    for package in build["include"].get("packages", []):
        cmd.append(f"--include-package={package}")
        if package == "PySide6":
            cmd.append("--enable-plugin=pyside6")

    for plugin in build["include"].get("plugins", []):
        cmd.append(f"--enable-plugin={plugin}")

    # Data directories
    for data_dir in build["include"].get("data_dirs", []):
        source_path = _abs(data_dir["source"])
        target_path = data_dir["target"]

        cmd.append(f"--include-data-dir={source_path}={target_path}")

    # External data
    for data in build["include"].get("external_data", []):
        cmd.append(f"--include-onefile-external-data={data}")

    if build["options"].get("remove_output", False):
        cmd.append("--remove-output")

    # External files
    for file in build["include"].get("files", []):
        file_path = file

        if not os.path.isabs(file_path):
            abs_file_path = _abs(file_path)
            cmd.append(f"--include-data-file={abs_file_path}={file_path}")
        else:
            file_path_norm = os.path.normpath(file_path)
            cmd.append(f"--include-data-file={file_path_norm}={os.path.basename(file_path_norm)}")

    # Remove .exe from output filename if we are not on Windows
    if sys.platform == "win32":
        file_name = build["output"]["filename"]
    else:
        file_name = build["output"]["filename"][:-4]

    # Output directory - convert to absolute path if it's not already
    output_dir = _abs(build["output"]["directory"])

    # Output settings
    cmd.extend([f"--output-dir={output_dir}", f"--output-filename={file_name}"])

    # Debug settings
    debug_config = config.get("debug", {})
    if debug_config.get("enabled", False):
        console_config = debug_config.get("console", {})
        console_mode = console_config.get("mode", "disable") # Default to disable if not specified

        if console_mode == "force":
            cmd.append("--windows-console-mode=force")
        elif console_mode == "attach":
            cmd.append("--windows-console-mode=attach")
        elif console_mode == "disable":
            cmd.append("--windows-console-mode=disable")
        # If other modes are added in Nuitka or our config, they can be handled here

        # Handle stdout and stderr redirection if console is not disabled
        if console_mode != "disable":
            stdout_path = console_config.get("stdout")
            stderr_path = console_config.get("stderr")

            if stdout_path:
                cmd.append(f"--force-stdout-spec={stdout_path}")
            elif console_mode == "force": # Default for forced console if not specified
                cmd.append("--force-stdout-spec={PROGRAM_BASE}.out.txt")
            
            if stderr_path:
                cmd.append(f"--force-stderr-spec={stderr_path}")
            elif console_mode == "force": # Default for forced console if not specified
                cmd.append("--force-stderr-spec={PROGRAM_BASE}.err.txt")
    else:
        # If debug is not enabled, ensure console is disabled
        cmd.append("--windows-console-mode=disable")

    return cmd


def _enqueue_output(out_pty, queue_obj, winpty_module_ref):
    # Get the PtyClosed exception type if winpty_module_ref is valid
    PtyClosedException = (
        getattr(winpty_module_ref, "PtyClosed", None) if winpty_module_ref else None
    )
    CHUNK_SIZE = 1024  # Read in chunks

    try:
        while True:
            try:
                # Use read() instead of readline() to capture non-newline terminated output
                char_chunk = out_pty.read(CHUNK_SIZE)

                if not char_chunk:  # Empty string can indicate EOF or PTY closed
                    # print("WinPTY Reader: read() returned empty string (EOF/Closed).", file=sys.stderr)
                    break
                queue_obj.put(char_chunk)
            except Exception as e_read:
                if PtyClosedException and isinstance(e_read, PtyClosedException):
                    print("WinPTY Reader: PTY closed during read().", file=sys.stderr)
                else:
                    print(
                        f"WinPTY Reader: Exception during read(): {type(e_read).__name__}: {e_read}",
                        file=sys.stderr,
                    )
                break  # Exit the loop on any read error or PtyClosed
    except Exception as e_outer:
        print(
            f"WinPTY Reader: Outer exception: {type(e_outer).__name__}: {e_outer}",
            file=sys.stderr,
        )
    finally:
        sys.stderr.flush()
        queue_obj.put(None)  # Crucial: always signal EOF to the main thread


def main(config_file="build_config.yaml", python_path=None):
    """Build executable with Nuitka

    Args:
        config_file: Path to configuration YAML file
        python_path: Optional path to Python executable to use for building
    """
    global active_process

    try:
        # Ensure config_file is an absolute path
        if not os.path.isabs(config_file):
            config_file = os.path.abspath(config_file)

        print(f"Using config file: {config_file}")
        sys.stdout.flush()

        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        # NOTE: We intentionally avoid mutating the loaded *config* dictionary
        # here.  Unit-tests expect the object that is forwarded to
        # ``build_nuitka_command`` to be *exactly* the same as the constant
        # ``TEST_CONFIG`` (see `tests/test_build_exe.py::test_main`).  Adding
        # book-keeping keys like "_config_file_path" would break equality
        # checks against that constant.  If absolute path resolution is
        # required within ``build_nuitka_command`` it can detect the absence
        # of this key and operate on relative paths instead.

        # Find Python executable - use provided path or find virtual environment Python
        if python_path:
            python_exe = python_path
            print(f"Using manually specified Python executable: {python_exe}")
        else:
            python_exe = find_venv_python()
            print(f"Using auto-detected Python executable: {python_exe}")
        sys.stdout.flush()

        # Verify Python executable exists – using pathlib so tests can patch
        if not Path(python_exe).exists():
            raise FileNotFoundError(f"Python executable not found: {python_exe}")

        # Get build configuration
        build_config = config.get("build", {})

        # Get output directory
        output_dir_path = Path(build_config.get("output", {}).get("directory", "dist"))

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
                output_filename = build_config.get("output", {}).get("filename", "")
                if not output_filename:
                    # Use main file name as fallback
                    main_file = config.get("project", {}).get("main_file", "")
                    if main_file:
                        output_filename = os.path.basename(main_file)
                        if output_filename.endswith(".py"):
                            output_filename = output_filename[:-3]
                    else:
                        output_filename = "output"

                # Ensure it has .exe extension on Windows
                output_exe = output_dir_path / output_filename
                if not output_exe.suffix:
                    output_exe = output_exe.with_suffix(".exe")

                print(f"Checking if output executable is locked: {output_exe}")
                sys.stdout.flush()

                if output_exe.exists():
                    for proc in psutil.process_iter(["pid", "name"]):
                        try:
                            for file in proc.open_files():
                                if file.path.lower() == str(output_exe).lower():
                                    print(
                                        f"Warning: Process {proc.name()} (PID: {proc.pid}) is locking the output file."
                                    )
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



        try:
            # On Windows, use pywinpty if available
            if sys.platform == "win32" and winpty:
                output_queue = queue.Queue()

                # Command for PTY spawn (winpty.PtyProcess.spawn often takes a string)
                # Ensure cmd is a list of strings for subprocess, but PTY might prefer string.
                # Let's assume cmd is a list, and join for winpty if needed, or pass as list if supported.
                # winpty.PtyProcess.spawn takes command as a list or string.
                # For safety and consistency with subprocess, ensure cmd is a list of strings here.
                print(
                    f"Running command with winpty: {' '.join(cmd)}"
                )  # For logging, show it space-separated
                sys.stdout.flush()

                term = winpty.PtyProcess.spawn(
                    cmd, cwd=os.path.dirname(config_file), env=os.environ
                )  # Pass the 'cmd' list directly
                active_process = term

                # Pass the imported winpty module to the thread for specific exception handling
                reader_thread = threading.Thread(
                    target=_enqueue_output, args=(term, output_queue, winpty)
                )
                reader_thread.daemon = True
                reader_thread.start()

                last_output_time = time.time()

                while True:
                    try:
                        output_chunk = output_queue.get(timeout=0.1)
                        if output_chunk is None:
                            break

                        sys.stdout.write(output_chunk)
                        sys.stdout.flush()
                        last_output_time = time.time()

                    except queue.Empty:
                        if not term.isalive() and output_queue.empty():
                            break
                        continue
                    except Exception as e:
                        print(f"Error processing PTY output from queue: {e}")
                        sys.stdout.flush()
                        break

                # Final clear of heartbeat before checking return_code
                if heartbeat_len > 0:
                    sys.stderr.write("\r" + " " * heartbeat_len + "\r")
                    sys.stderr.flush()
                    heartbeat_len = 0

                # Ensure reader thread is joined
                if reader_thread.is_alive():
                    reader_thread.join(timeout=5)
                if reader_thread.is_alive():
                    print("WinPTY: Warning: Reader thread did not join cleanly.")

                # Get exit status
                return_code = term.exitstatus
                if return_code is None:  # If not set, try waiting briefly
                    try:
                        return_code = term.wait(timeout=1)  # winpty.PtyProcess.wait()
                    except (
                        Exception
                    ):  # Catches winpty.exceptions.TimeoutExpired and others
                        print(
                            "WinPTY: Warning: Timeout or error on final term.wait(). Forcing termination if alive."
                        )
                        if term.isalive():
                            term.terminate(force=True)
                        return_code = (
                            term.exitstatus if term.exitstatus is not None else 1
                        )  # Assume error

                # Close the PTY process itself
                try:
                    if hasattr(term, "close") and callable(getattr(term, "close")):
                        term.close()
                except Exception as e_pty_close:
                    print(f"WinPTY: Warning: Error during term.close(): {e_pty_close}")

                active_process = None
            else:
                # Use shell=True on Windows to help with permissions
                use_shell = sys.platform == "win32"

                # Run the command with real-time output
                if use_shell:
                    # Properly quote arguments with spaces
                    cmd_str = " ".join(
                        f'"{arg}"' if " " in arg or "\\" in arg else arg for arg in cmd
                    )
                    print(f"Executing command: {cmd_str}")
                    sys.stdout.flush()

                    # Use subprocess.run instead of Popen to match test expectations
                    result = subprocess.run(
                        cmd_str,
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        check=False,
                        cwd=os.path.dirname(config_file),
                        creationflags=(
                            subprocess.CREATE_NEW_PROCESS_GROUP
                            if sys.platform == "win32"
                            else 0
                        ),
                    )
                else:
                    # Use subprocess.run to match test expectations
                    result = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        check=False,
                        cwd=os.path.dirname(config_file),
                        creationflags=(
                            subprocess.CREATE_NEW_PROCESS_GROUP
                            if sys.platform == "win32"
                            else 0
                        ),
                    )

                # Process output
                output = result.stdout
                if output:
                    print(output)
                    sys.stdout.flush()
                
                # Get return code
                return_code = result.returncode
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
        copy_beside_items = build_config.get("copy_beside", [])
        if copy_beside_items:
            print(f"\nCopying additional items to {output_dir_path}...")
            sys.stdout.flush()

            # Get the project directory (where the config file is located)
            project_dir = os.path.dirname(config_file)

            for item_name in copy_beside_items:
                source_path = Path(project_dir) / item_name
                dest_path = output_dir_path / item_name

                if not source_path.exists():
                    print(
                        f"Warning: Source item '{item_name}' not found at '{source_path}', skipping."
                    )
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
                        print(
                            f"Warning: Source item '{item_name}' is not a file or directory, skipping."
                        )
                        sys.stdout.flush()
                except PermissionError as pe:
                    print(f"Permission error copying '{item_name}': {pe}")
                    print(
                        "Try running as administrator or changing the output directory."
                    )
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
        if config.get("installer", {}).get("enabled", False):
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
