#!/usr/bin/env python3
import html
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

import yaml


def build_wix_installer(config: Dict[str, Any]) -> None:
    """Build the WiX installer if enabled in config."""
    if not check_wix_installed():
        print("Error: Wix Toolset not found. Please install it from:")
        print("https://docs.firegiant.com/wix/")
        sys.exit(1)

    if not check_wix_extension():
        install_wix_extension()

    if not config.get("installer", {}).get("enabled", False):
        return

    installer = config["installer"]
    build = config["build"]

    # Ensure required directories exist
    output_dir = Path(installer["output"]["directory"])
    output_dir.mkdir(exist_ok=True)

    # Check if the executable exists
    exe_path = Path(build["output"]["directory"]) / build["output"]["filename"]
    if not exe_path.exists():
        print(f"Error: Executable not found at {exe_path}")
        print("Please build the executable first.")
        return

    # Generate WiX source file
    wxs_content = generate_wix_source(config)
    wxs_path = output_dir / "installer.wxs"
    with open(wxs_path, "w") as f:
        f.write(wxs_content)

    try:
        # Get paths from config
        ui_dir = Path(installer["ui"].get("banner_image", ".")).parent
        license_path = Path(installer["license_file"]).parent

        # Build MSI using WiX
        cmd = [
            "wix",
            "build",
            str(wxs_path),
            "-bindpath",
            f"BinDir={build['output']['directory']}",
            "-bindpath",
            f"UiImagesDir={ui_dir}",
            "-bindpath",
            f"LicenseDir={license_path}",
            "-ext",
            "WixToolset.UI.wixext",
            "-o",
            str(output_dir / installer["output"]["filename"]),
        ]

        subprocess.run(cmd, check=True)
        print("WiX installer built successfully!")

    except subprocess.CalledProcessError as e:
        print(f"Error building installer: {e}")
        raise


def generate_wix_source(config: Dict[str, Any]) -> str:
    """Generate WiX source file content."""
    installer = config["installer"]
    project = config["project"]
    build = config["build"]
    build_output_dir = Path(build["output"]["directory"])
    manufacturer_sanitized = html.escape(installer["metadata"]["manufacturer"])
    product_name_sanitized = html.escape(installer["metadata"]["product_name"])

    def sanitize_wix_id(name: str) -> str:
        """Replace invalid WiX ID characters with underscores, ensure valid start, and limit length."""
        sanitized = re.sub(r"[^a-zA-Z0-9.]+", "_", name)
        sanitized = sanitized.strip("_")
        if not sanitized or not re.match(r"^[a-zA-Z_]", sanitized):
            sanitized = "_" + sanitized
        return sanitized[:70]

    # --- Generate XML for items to copy beside ---
    copy_beside_items = build.get("copy_beside", [])
    # Placeholders for generated XML parts
    copy_beside_dir_and_comp_xml = ""  # Will hold combined <Directory><Component>...</Component></Directory> structure
    copy_beside_component_refs_xml = ""  # For <Feature>

    for item_name in copy_beside_items:
        item_source_path_on_disk = build_output_dir / item_name
        component_id = sanitize_wix_id(f"Comp_{item_name}")
        base_directory_id = sanitize_wix_id(f"Dir_{item_name}")

        if not item_source_path_on_disk.exists():
            print(
                f"Warning (WiX): Source item '{item_name}' not found in build output dir '{build_output_dir}', skipping."
            )
            continue

        if item_source_path_on_disk.is_dir():
            # 1. First, build a complete map of all directories and their IDs
            directory_id_map = {}  # Maps relative paths to their WiX IDs

            # Add the base directory to the map
            directory_id_map[Path(".")] = base_directory_id

            # Walk to identify all directories first
            for root, dirs, _ in os.walk(item_source_path_on_disk):
                current_dir_path = Path(root)
                relative_dir_path = current_dir_path.relative_to(
                    item_source_path_on_disk
                )

                # Skip the base directory (already added)
                if relative_dir_path == Path("."):
                    continue

                # Create a unique ID for this directory
                dir_rel_path_str = relative_dir_path.as_posix().replace("/", "_")
                dir_id = sanitize_wix_id(f"{base_directory_id}_{dir_rel_path_str}")
                directory_id_map[relative_dir_path] = dir_id

            # 2. Build the directory structure XML using a simpler approach
            # Start with the base directory
            directory_xml = (
                f'\n  <Directory Id="{base_directory_id}" Name="{item_name}">'
            )

            # Function to recursively build directory structure
            def build_directory_structure(parent_path, indent="  "):
                result = ""
                # Find all immediate children of this parent
                children = [
                    p
                    for p in directory_id_map.keys()
                    if p != Path(".") and p.parent == parent_path
                ]

                for child_path in sorted(children):
                    child_id = directory_id_map[child_path]
                    child_name = child_path.name

                    # Start this directory element
                    result += (
                        f'\n{indent}  <Directory Id="{child_id}" Name="{child_name}">'
                    )

                    # Recursively add its children
                    result += build_directory_structure(child_path, indent + "  ")

                    # Close this directory element
                    result += f"\n{indent}  </Directory>"

                return result

            # Build the nested structure starting from the root
            directory_xml += build_directory_structure(Path("."))

            # 3. Create components for each directory with its files
            components_xml = ""
            component_refs_xml = ""

            # Walk the directory structure again to create components for each directory with files
            for root, _, files in os.walk(item_source_path_on_disk):
                # Skip if no files in this directory
                if not files:
                    continue

                current_dir_path = Path(root)
                relative_dir_path = current_dir_path.relative_to(
                    item_source_path_on_disk
                )

                # Get the directory ID from our map
                dir_id = directory_id_map.get(relative_dir_path, base_directory_id)

                # Create a unique component ID for this directory
                dir_component_id = sanitize_wix_id(
                    f"Comp_{item_name}_{relative_dir_path.as_posix().replace('/', '_')}"
                )

                # Build file elements for this directory
                files_xml = ""
                for file in files:
                    file_id = sanitize_wix_id(f"File_{dir_component_id}_{file}")
                    # Source path relative to BinDir
                    file_source_rel_bindir = Path(item_name) / relative_dir_path / file
                    files_xml += f"""
          <File Id="{file_id}" Name="{file}" Source="!(bindpath.BinDir)\\{file_source_rel_bindir.as_posix()}" />"""

                # Create a component for this directory and its files
                create_folder_xml = (
                    "\n      <CreateFolder/>" if relative_dir_path == Path(".") else ""
                )
                remove_folder_xml = ""
                if relative_dir_path == Path("."):
                    # Only add RemoveFolder for the base directory
                    remove_folder_id = sanitize_wix_id(f"Remove_{dir_id}")
                    remove_folder_xml = f'\n      <RemoveFolder Id="{remove_folder_id}" On="uninstall"/>'

                # Create the component definition
                component_xml = f"""
    <Component Id="{dir_component_id}" Guid="{str(uuid.uuid4())}" Directory="{dir_id}">{create_folder_xml}{files_xml}{remove_folder_xml}
      <RegistryValue Root="HKCU" Key="Software\\{manufacturer_sanitized}\\{product_name_sanitized}\\{dir_component_id}" Name="installed" Type="integer" Value="1" KeyPath="yes" />
    </Component>"""

                components_xml += component_xml
                component_refs_xml += (
                    f'\n            <ComponentRef Id="{dir_component_id}" />'
                )

            # 4. Combine everything - first the directory structure, then components
            directory_xml += components_xml

            # Close the base directory element
            directory_xml += "\n  </Directory>"

            # Append the complete XML for this item
            copy_beside_dir_and_comp_xml += directory_xml
            # Add the component references for the Feature list
            copy_beside_component_refs_xml += component_refs_xml

        elif item_source_path_on_disk.is_file():
            # Generate component for a single file directly under INSTALLFOLDER
            file_id = sanitize_wix_id(f"File_{component_id}_{item_name}")
            file_source_rel_bindir = Path(item_name)
            # Note: Single files don't need a separate <Directory> element, component goes direct in INSTALLFOLDER
            single_file_component = f"""
    <Component Id="{component_id}" Guid="{str(uuid.uuid4())}" Directory="INSTALLFOLDER">
        <File Id="{file_id}"
              Name="{item_name}"
              Source="!(bindpath.BinDir)\\{file_source_rel_bindir.as_posix()}"
              KeyPath="yes"/>
    </Component>"""
            # Add the component XML directly (no surrounding Directory needed here)
            copy_beside_dir_and_comp_xml += single_file_component
            # Add component ref
            copy_beside_component_refs_xml += (
                f'\n            <ComponentRef Id="{component_id}" />'
            )

    # --- End generate XML ---

    # Only include URL properties if they are set
    url_properties = ""
    if project.get("url"):
        url_properties = f"""
        <Property Id="ARPURLINFOABOUT" Value="{project['url']}" />
        <Property Id="ARPHELPLINK" Value="{project['url']}" />"""

    # Get relative paths for resources
    banner_path = Path(installer["ui"]["banner_image"]).name
    dialog_path = Path(installer["ui"]["dialog_image"]).name
    license_path = Path(installer["license_file"]).name

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs"
     xmlns:ui="http://wixtoolset.org/schemas/v4/wxs/ui"
     xmlns:util="http://wixtoolset.org/schemas/v4/wxs/util">
    <Package
        Name="{product_name_sanitized}"
        Manufacturer="{manufacturer_sanitized}"
        Version="{project['version']}"
        UpgradeCode="{installer['metadata']['upgrade_code']}"
        Scope="perMachine">
        
        <MajorUpgrade DowngradeErrorMessage="A newer version of [ProductName] is already installed." />
        <MediaTemplate EmbedCab="yes" />
        
        <!-- Application Icon -->
        {f'<Icon Id="app.ico" SourceFile="{project["icon"]}" />' if project.get('icon') else ''}
        {f'<Property Id="ARPPRODUCTICON" Value="app.ico" />' if project.get('icon') else ''}
        
        <!-- Add/Remove Programs Information -->{url_properties}
        <Property Id="ARPCOMMENTS" Value="{project['description']}" />
        <Property Id="ARPCONTACT" Value="{manufacturer_sanitized}" />
        <Property Id="ARPNOREPAIR" Value="1" />
        
        <!-- Directory Structure -->
        <StandardDirectory Id="ProgramFiles64Folder">
            <Directory Id="INSTALLFOLDER" Name="{product_name_sanitized}">
                <Component Id="MainExecutable" Guid="{str(uuid.uuid4())}">
                    <File Id="MainEXE"
                          Name="{build['output']['filename']}"
                          Source="!(bindpath.BinDir)\\{build['output']['filename']}"
                          KeyPath="yes">
                        <!-- Grant read and execute permissions to all users -->
                        <Permission User="Everyone" GenericAll="yes" />
                    </File>
                    
                    <!-- Register application -->
                    <RegistryValue Root="HKLM"
                                 Key="Software\\{product_name_sanitized}"
                                 Name="InstallPath"
                                 Type="string"
                                 Value="[INSTALLFOLDER]" />
                </Component>

                {copy_beside_dir_and_comp_xml}
            </Directory>
        </StandardDirectory>
        
        <!-- Start Menu -->
        <StandardDirectory Id="ProgramMenuFolder">
            <Directory Id="ApplicationProgramsFolder" Name="{product_name_sanitized}">
                <Component Id="ApplicationShortcuts" Guid="{str(uuid.uuid4())}">
                    <Shortcut Id="ApplicationShortcut"
                             Name="{product_name_sanitized}"
                             Description="{project['description']}"
                             Target="[INSTALLFOLDER]{build['output']['filename']}"
                             WorkingDirectory="INSTALLFOLDER"
                             Icon="app.ico" />
                    <Shortcut Id="UninstallProduct"
                             Name="Uninstall {product_name_sanitized}"
                             Description="Uninstall {product_name_sanitized}"
                             Target="[SystemFolder]msiexec.exe"
                             Arguments="/x [ProductCode]"/>
                    <RemoveFolder Id="CleanUpShortCut" Directory="ApplicationProgramsFolder" On="uninstall" />
                    <RegistryValue Root="HKCU"
                                 Key="Software\\{manufacturer_sanitized}\\{product_name_sanitized}"
                                 Name="installed"
                                 Type="integer"
                                 Value="1"
                                 KeyPath="yes" />
                </Component>
            </Directory>
        </StandardDirectory>
        
        <!-- Features -->
        <Feature Id="ProductFeature" Title="{product_name_sanitized}" Level="1">
            <ComponentRef Id="MainExecutable" />
            <ComponentRef Id="ApplicationShortcuts" />
            {copy_beside_component_refs_xml}
        </Feature>
        
        <!-- UI -->
        <Property Id="WIXUI_INSTALLDIR" Value="INSTALLFOLDER" />
        <Property Id="WIXUI_EXITDIALOGOPTIONALTEXT" Value="Thank you for installing {product_name_sanitized}." />
        
        <!-- Custom UI Images -->
        <WixVariable Id="WixUIDialogBmp" Value="!(bindpath.UiImagesDir)\\{dialog_path}" />
        <WixVariable Id="WixUIBannerBmp" Value="!(bindpath.UiImagesDir)\\{banner_path}" />

        <!-- License -->
        <WixVariable Id="WixUILicenseRtf" Value="!(bindpath.LicenseDir)\\{license_path}" />
        
        <ui:WixUI Id="WixUI_InstallDir" />
        
    </Package>
</Wix>"""


def check_wix_installed() -> bool:
    """Check if Wix Toolset is installed."""
    try:
        subprocess.run(["wix", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def check_wix_extension() -> bool:
    """Check if Wix UI extension is installed."""
    try:
        result = subprocess.run(
            ["wix", "extension", "list"], capture_output=True, text=True, check=True
        )
        return "WixToolset.UI.wixext" in result.stdout
    except subprocess.CalledProcessError:
        return False


def install_wix_extension() -> None:
    """Install Wix UI extension if missing."""
    try:
        print("Installing Wix UI extension...")
        subprocess.run(["wix", "extension", "add", "WixToolset.UI.wixext"], check=True)
        print("Extension installed successfully")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install Wix UI extension: {e}")
        sys.exit(1)


def main(config_file="build_config.yaml"):
    """Create Windows installer using Wix

    Args:
        config_file: Path to configuration YAML file
    """
    try:
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: Configuration file '{config_file}' not found.")
        return

    build_wix_installer(config)


if __name__ == "__main__":
    # Maintain backward compatibility when run directly
    main()
