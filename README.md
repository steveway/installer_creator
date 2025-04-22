# Installer Creator

Python package for compiling projects with Nuitka and creating Windows installers with Wix Toolset.

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

## Usage

```bash
# Generate UUID for upgrade code
installer-creator generate-uuid -s "MyAppName"

# Build executable
installer-creator build-exe -c build_config.yaml

# Create installer
installer-creator build-installer -c build_config.yaml
```

## Testing

The project includes a comprehensive test suite to ensure functionality works as expected. The tests cover:

- Building executables with Nuitka
- Creating Windows installers with WiX Toolset
- Handling special characters in manufacturer and product names
- Configuration validation and loading

To run the tests:

```bash
# Install test dependencies
pip install pytest

# Run all tests
python tests/run_tests.py

# Or run specific tests
pytest tests/test_build_exe.py
pytest tests/test_build_installer.py
```

The test suite uses mocking to simulate the build environment, so you don't need to have WiX Toolset installed to run the tests.
