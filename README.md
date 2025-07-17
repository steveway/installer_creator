# Installer Creator

Python package for compiling projects with Nuitka and creating Windows installers with Wix Toolset.

## Features

- **One-click builds** - Compile Python to EXE and create installers with a single command
- **Graphical Config Editor** - Intuitive UI for managing build configurations
- **Python Path Selection** - Manually specify Python executable or auto-detect virtual environments
- **File Browser Integration** - Easily add files and resources with a file dialog
- **Real-time Progress** - View build output and cancel long-running operations
- **Upgrade Code Management** - Generate consistent UUIDs for product upgrades
- **Flexible Configuration** - Support for complex build scenarios and dependencies

## Installation

```bash
pip install installer-creator
```

## Prerequisites

1. **Wix Toolset** (required for installer creation):
   ```bash
   dotnet tool install --global wix
   ```
   The package will automatically install the Wix UI extension if missing

2. **Nuitka** (will be installed automatically)

## Quick Start

1. Create a basic configuration:
   ```bash
   installer-creator config-editor
   ```
2. Build your application:
   ```bash
   installer-creator build-exe
   ```
3. Create an installer:
   ```bash
   installer-creator build-installer
   ```

## Configuration Editor GUI

The graphical interface provides:

- **Visual Editing** of all configuration options
- **Live Preview** of installer appearance
- **One-click Builds** directly from the interface
- **Validation** of configuration values
- **Progress Monitoring** with cancel option

Launch the GUI with:
```bash
installer-creator config-editor
```

## Command Line Usage

```bash
# Generate deterministic UUID for upgrade codes
installer-creator generate-uuid -s "MyAppName"

# Build executable with custom config
installer-creator build-exe -c custom_config.yaml --verbose

# Build with specific Python executable
installer-creator build-exe --python-path "C:\\path\\to\\python.exe"

# Create installer with debug output
installer-creator build-installer -c custom_config.yaml --debug
```

## Troubleshooting

**Common Issues:**

- **Missing Wix Toolset**: Ensure `wix` is installed globally
- **Permission Errors**: Run commands as administrator when needed
- **Build Failures**: Check paths and file permissions in configuration

For detailed debugging:
```bash
installer-creator build-exe --debug
```

## Testing

The project includes comprehensive tests covering:

- Build process validation
- Installer creation
- Configuration handling
- Edge case scenarios

Run tests with:
```bash
pytest tests/
```

## Configuration

Create a `build_config.yaml` file:

```yaml
# Nuitka build configuration
project:
  name: "MyApp"  # Application name
  version: "1.0.0"
  description: "My Application Description"
  company: ""
  icon: "app_icon.ico"  # Optional icon file
  main_file: "main.py"  # Entry point file
  python_path: ""  # Optional path to Python executable (auto-detected if empty)

build:
  output:
    directory: "dist"
    filename: "my_app.exe"  # Output executable name
  options:
    standalone: true
    onefile: true
    splash_screen: ""
    remove_output: true
  include:
    packages:
      - ""
    plugins:
      - ""
    data_dirs:
      - source: "resources"
        target: "resources"
    external_data:
      - "*.dll"
    files:
      - "requirements.txt"
      - "readme.md"
      - "*.json"  # Example wildcard pattern
  copy_beside:
    - "resources"

installer:
  enabled: true
  output:
    directory: "dist"
    filename: "my_app_installer.msi"
  metadata:
    manufacturer: "Your Company"
    product_name: "My Application"
    upgrade_code: ""  # Generate with installer-creator generate-uuid
  ui:
    banner_image: "banner.bmp"
    dialog_image: "dialog.bmp"
  license_file: "license.rtf"
  shortcuts:
    desktop: true
    start_menu: true

debug:
  enabled: false
  console:
    mode: "disabled"
    stdout: null
    stderr: null

exclude:
  - "__pycache__"
  - "*.pyc"
  - "*.pyo"
  - "*.pyd"
  - "build"
  - "dist"

```

## Resource Paths

The installer supports flexible resource locations:
- UI images (banner/dialog) can be anywhere
- License file can be anywhere
- Paths can be absolute or relative to config file

## GUI Features

The configuration editor provides a graphical interface for:
- Editing all aspects of the build configuration
- Managing project details, build settings, and installer options
- Previewing configuration changes
- Building executables and installers directly from the GUI
- Viewing real-time build output
- Canceling builds in progress
- Specifying a custom Python executable path
- Browsing and selecting files with a native file dialog

To launch the GUI, simply run:
```bash
installer-creator config-editor
```

The GUI will automatically create a default configuration file if none exists.
