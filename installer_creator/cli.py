import argparse
import os
import sys
import traceback

from rich.console import Console
from rich.theme import Theme
from rich.traceback import install as install_rich_traceback

from . import (
    build_exe,
    build_installer,
    changelog_generator,
    config_editor,
    uuid_generator,
)
from .__init__ import __version__

install_rich_traceback(show_locals=True)
custom_theme = Theme({"info": "dim cyan", "warning": "magenta", "error": "bold red"})
console = Console(theme=custom_theme)


def main():
    parser = argparse.ArgumentParser(description="Python Installer Creator Tool")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Build EXE command
    build_exe_parser = subparsers.add_parser(
        "build-exe", help="Build executable with Nuitka"
    )
    build_exe_parser.add_argument(
        "-c", "--config", default="build_config.yaml", help="Configuration file"
    )
    build_exe_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )
    build_exe_parser.add_argument(
        "--debug", action="store_true", help="Enable debug mode"
    )
    build_exe_parser.add_argument(
        "--python-path", help="Path to Python executable to use for building"
    )

    # Build Installer command
    build_installer_parser = subparsers.add_parser(
        "build-installer", help="Create Windows installer"
    )
    build_installer_parser.add_argument(
        "-c", "--config", default="build_config.yaml", help="Configuration file"
    )
    build_installer_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )
    build_installer_parser.add_argument(
        "--debug", action="store_true", help="Enable debug mode"
    )

    # Generate Changelog command
    subparsers.add_parser("generate-changelog", help="Generate changelog")

    # Generate UUID command
    uuid_parser = subparsers.add_parser(
        "generate-uuid", help="Generate UUID for upgrade codes"
    )
    uuid_parser.add_argument(
        "-s", "--string", help="String to generate deterministic UUID from"
    )
    uuid_parser.add_argument(
        "-r",
        "--random",
        action="store_true",
        default=True,
        help="Generate random UUID (default)",
    )

    # Config Editor GUI command
    config_editor_parser = subparsers.add_parser(
        "config-editor", help="Launch configuration editor GUI"
    )
    config_editor_parser.add_argument(
        "-c", "--config", default="build_config.yaml", help="Configuration file to edit"
    )

    args = parser.parse_args()

    # Set common environment variables early
    if hasattr(args, "verbose") and args.verbose:
        os.environ["INSTALLER_CREATOR_VERBOSE"] = "1"
        console.print("[info]Verbose mode enabled.[/info]")
    if hasattr(args, "debug") and args.debug:
        os.environ["INSTALLER_CREATOR_DEBUG"] = "1"
        console.print(
            "[info]Debug mode enabled (rich traceback handler active).[/info]"
        )
    else:
        try:
            from rich.traceback import install as install_rich_traceback

            install_rich_traceback(
                show_locals=False, suppress=[]
            )  # Disable locals, don't suppress anything specific initially
        except ImportError:
            pass  # rich not installed, nothing to disable

    try:  # Main command execution block
        if args.command == "build-exe":
            console.print(f"[info]Starting build-exe with config: {args.config}[/info]")
            build_exe.main(config_file=args.config, python_path=args.python_path)
            console.print("[info]Build executable finished.[/info]")

        elif args.command == "build-installer":
            console.print(
                f"[info]Starting build-installer with config: {args.config}[/info]"
            )
            build_installer.main(config_file=args.config)
            console.print("[info]Build installer finished.[/info]")

        elif args.command == "generate-changelog":
            console.print("[info]Generating changelog...[/info]")
            changelog_generator.main()
            console.print("[info]Changelog generation finished.[/info]")

        elif args.command == "generate-uuid":
            if args.string:
                generated_uuid = uuid_generator.generate_deterministic_uuid(args.string)
                console.print(
                    f"Deterministic UUID for '{args.string}': [bold cyan]{generated_uuid}[/bold cyan]"
                )
            elif args.random:
                generated_uuid = uuid_generator.generate_random_uuid()
                console.print(f"Random UUID: [bold cyan]{generated_uuid}[/bold cyan]")
            # No need for else, as --random defaults to True

        elif args.command == "config-editor":
            console.print(
                f"[info]Launching Config Editor with config: {args.config}...[/info]"
            )
            config_editor.run(config_file=args.config)  # This likely starts a GUI loop
            console.print("[info]Config Editor closed.[/info]")

        # Add other commands here

    except Exception as e:
        if hasattr(args, "debug") and args.debug:
            console.print("[error]An error occurred:[/error]")
            console.print_exception(show_locals=True)  # Rich traceback with locals
        else:
            # Simple error message for non-debug mode
            console.print(f"[error]Error:[/error] {e}")
            # Optionally print a hint to use --debug for more details
            console.print("Run with --debug for more details.")

        sys.exit(1)  # Exit with error code

    sys.exit(0)  # Success


if __name__ == "__main__":
    main()
