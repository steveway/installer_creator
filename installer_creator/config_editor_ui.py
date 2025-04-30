#!/usr/bin/env python3
"""
Configuration Editor UI for installer-creator

This module provides a GUI for editing build_config.yaml files using Qt Designer UI.
"""
import os
import sys
import uuid
import yaml
import subprocess
import threading
import queue
from queue import Empty
import time
import re
from pathlib import Path
from typing import Dict, Any, Optional, List, Union

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, 
    QMessageBox, QInputDialog, QDialog, QListWidgetItem, QMenu, 
    QVBoxLayout, QProgressBar, QLabel, QFormLayout, QLineEdit, QPushButton, QDialogButtonBox, QHBoxLayout
)
from PySide6.QtGui import QAction, QIcon
from PySide6.QtCore import Qt, QFile, QIODevice, Slot, Signal, QObject, QTimer
from PySide6.QtUiTools import QUiLoader

# Import pywinpty for Windows
if sys.platform == 'win32':
    try:
        import winpty
    except ImportError:
        print("Warning: pywinpty not installed. Windows terminal handling may be limited.")
        winpty = None
else:
    # On non-Windows platforms, set winpty to None
    winpty = None

class CommandSignals(QObject):
    """Signals for command execution"""
    output = Signal(str)
    progress = Signal(int)
    status = Signal(str)
    finished = Signal(int)


class ProgressDialog(QDialog):
    """Dialog for showing build progress."""
    
    def __init__(self, parent=None, title="Building..."):
        super().__init__(parent)
        
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        
        
        layout = QVBoxLayout(self)
        
        self.status_label = QLabel("Initializing...")
        layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        # Add a cancel button
        self.cancel_button = QPushButton("Cancel")
        layout.addWidget(self.cancel_button)
        
        # Don't allow closing with X button
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)
    
    def update_progress(self, value):
        """Update the progress bar."""
        self.progress_bar.setValue(value)
    
    def update_status(self, text):
        """Update the status text."""
        self.status_label.setText(text)


class DataDirDialog(QDialog):
    """Dialog for adding/editing data directory items."""
    
    def __init__(self, parent=None, source="", target=""):
        super().__init__(parent)
        
        self.setWindowTitle("Data Directory")
        
        layout = QFormLayout(self)
        
        self.source_edit = QLineEdit(source)
        icon_path = os.path.join(os.path.dirname(__file__), "installer_creator_logo.png")
        self.setWindowIcon(QIcon(icon_path))
        source_layout = QHBoxLayout()
        source_layout.addWidget(self.source_edit)
        source_browse = QPushButton("Browse...")
        source_browse.clicked.connect(self.browse_source)
        source_layout.addWidget(source_browse)
        
        self.target_edit = QLineEdit(target)
        
        layout.addRow("Source:", source_layout)
        layout.addRow("Target:", self.target_edit)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        
        layout.addRow(buttons)
    
    def browse_source(self):
        """Browse for source directory."""
        dir_path = QFileDialog.getExistingDirectory(self, "Select Source Directory")
        if dir_path:
            self.source_edit.setText(dir_path)
            # Set default target to be the same as source basename
            if not self.target_edit.text():
                self.target_edit.setText(os.path.basename(dir_path))


class ConfigEditorWindow(QMainWindow):
    """Main window for the build configuration editor using Qt Designer UI."""
    
    def __init__(self, config_file=None):
        super().__init__()
        
        self.config_file = config_file or 'build_config.yaml'
        self.config_data = {}
        self.active_process = None  # Store the active process for cancellation
        self.is_building = False  # Flag to prevent duplicate builds
        
        # Load UI from file
        ui_file = QFile(os.path.join(os.path.dirname(__file__), "config_editor.ui"))
        if not ui_file.open(QIODevice.ReadOnly):
            raise RuntimeError(f"Cannot open {ui_file.fileName()}: {ui_file.errorString()}")
        
        loader = QUiLoader()
        self.ui = loader.load(ui_file)
        icon_path = os.path.join(os.path.dirname(__file__), "installer_creator_logo.png")
        self.ui.setWindowIcon(QIcon(icon_path))
        ui_file.close()
        
        if not self.ui:
            raise RuntimeError(loader.errorString())
        
        # Set up the UI
        self.setup_ui()
        
        # Load configuration
        self.load_config()
        
        # Show the UI
        self.ui.show()
    
    def setup_ui(self):
        """Set up the UI connections and additional widgets."""
        # Connect browse buttons
        self.ui.iconBrowseButton.clicked.connect(
            lambda: self.browse_file(self.ui.projectIcon, "Icon Files (*.ico)"))
        self.ui.mainFileBrowseButton.clicked.connect(
            lambda: self.browse_file(self.ui.projectMainFile, "Python Files (*.py)"))
        self.ui.outputDirBrowseButton.clicked.connect(
            lambda: self.browse_directory(self.ui.buildOutputDir))
        self.ui.splashScreenBrowseButton.clicked.connect(
            lambda: self.browse_file(self.ui.buildSplashScreen, "Image Files (*.png *.jpg *.bmp)"))
        self.ui.installerOutputDirBrowseButton.clicked.connect(
            lambda: self.browse_directory(self.ui.installerOutputDir))
        self.ui.bannerImageBrowseButton.clicked.connect(
            lambda: self.browse_file(self.ui.installerBannerImage, "BMP Files (*.bmp)"))
        self.ui.dialogImageBrowseButton.clicked.connect(
            lambda: self.browse_file(self.ui.installerDialogImage, "BMP Files (*.bmp)"))
        self.ui.licenseFileBrowseButton.clicked.connect(
            lambda: self.browse_file(self.ui.installerLicenseFile, "RTF Files (*.rtf)"))
        
        # Connect UUID generator
        self.ui.generateUuidButton.clicked.connect(self.generate_uuid)
        
        # Connect save and load buttons
        self.ui.saveButton.clicked.connect(self.save_config)
        self.ui.loadButton.clicked.connect(self.browse_config)
        
        # Connect build buttons
        self.ui.buildExeButton.clicked.connect(self.build_executable)
        self.ui.buildInstallerButton.clicked.connect(self.build_installer)
        self.ui.clearOutputButton.clicked.connect(self.clear_output)
        
        # Set up debug console mode combobox
        self.ui.debugConsoleMode.addItems(["disabled", "enabled", "force"])
        
        # Set up list widgets
        self.setup_list_widgets()
    
    def setup_list_widgets(self):
        """Set up the list widgets for packages, plugins, etc."""
        # Packages
        self.ui.packagesAddButton.clicked.connect(lambda: self.add_list_item(self.ui.packagesList))
        self.ui.packagesEditButton.clicked.connect(lambda: self.edit_list_item(self.ui.packagesList))
        self.ui.packagesRemoveButton.clicked.connect(lambda: self.remove_list_item(self.ui.packagesList))
        
        # Plugins
        self.ui.pluginsAddButton.clicked.connect(lambda: self.add_list_item(self.ui.pluginsList))
        self.ui.pluginsEditButton.clicked.connect(lambda: self.edit_list_item(self.ui.pluginsList))
        self.ui.pluginsRemoveButton.clicked.connect(lambda: self.remove_list_item(self.ui.pluginsList))
        
        # Data directories
        self.ui.dataDirsAddButton.clicked.connect(self.add_data_dir)
        self.ui.dataDirsEditButton.clicked.connect(self.edit_data_dir)
        self.ui.dataDirsRemoveButton.clicked.connect(lambda: self.remove_list_item(self.ui.dataDirsList))
        
        # External data
        if hasattr(self.ui, 'externalDataAddButton'):
            self.ui.externalDataAddButton.clicked.connect(lambda: self.add_list_item(self.ui.externalDataList))
            self.ui.externalDataEditButton.clicked.connect(lambda: self.edit_list_item(self.ui.externalDataList))
            self.ui.externalDataRemoveButton.clicked.connect(lambda: self.remove_list_item(self.ui.externalDataList))
        
        # Files
        if hasattr(self.ui, 'filesAddButton'):
            self.ui.filesAddButton.clicked.connect(lambda: self.add_list_item(self.ui.filesList))
            self.ui.filesEditButton.clicked.connect(lambda: self.edit_list_item(self.ui.filesList))
            self.ui.filesRemoveButton.clicked.connect(lambda: self.remove_list_item(self.ui.filesList))
        
        # Copy beside
        if hasattr(self.ui, 'copyBesideAddButton'):
            self.ui.copyBesideAddButton.clicked.connect(lambda: self.add_file_or_dir_item(self.ui.copyBesideList))
            self.ui.copyBesideEditButton.clicked.connect(lambda: self.edit_file_or_dir_item(self.ui.copyBesideList))
            self.ui.copyBesideRemoveButton.clicked.connect(lambda: self.remove_list_item(self.ui.copyBesideList))
        
        # Exclude
        self.ui.excludeAddButton.clicked.connect(lambda: self.add_list_item(self.ui.excludeList))
        self.ui.excludeEditButton.clicked.connect(lambda: self.edit_list_item(self.ui.excludeList))
        self.ui.excludeRemoveButton.clicked.connect(lambda: self.remove_list_item(self.ui.excludeList))
        
        # Build buttons
        self.ui.buildExeButton.clicked.connect(self.build_executable)
        self.ui.buildInstallerButton.clicked.connect(self.build_installer)
        self.ui.clearOutputButton.clicked.connect(self.clear_output)
    
    def add_data_dir(self):
        """Add a new data directory item."""
        dialog = DataDirDialog(self.ui)
        if dialog.exec():
            source = dialog.source_edit.text()
            target = dialog.target_edit.text()
            if source and target:
                item_text = f"{source} -> {target}"
                list_item = QListWidgetItem(item_text)
                list_item.setData(Qt.UserRole, {"source": source, "target": target})
                self.ui.dataDirsList.addItem(list_item)
    
    def edit_data_dir(self):
        """Edit the selected data directory item."""
        current_item = self.ui.dataDirsList.currentItem()
        if current_item:
            data = current_item.data(Qt.UserRole)
            dialog = DataDirDialog(self.ui, data["source"], data["target"])
            
            if dialog.exec():
                source = dialog.source_edit.text()
                target = dialog.target_edit.text()
                if source and target:
                    item_text = f"{source} -> {target}"
                    current_item.setText(item_text)
                    current_item.setData(Qt.UserRole, {"source": source, "target": target})
    
    def get_data_dirs(self):
        """Get all data directory items."""
        items = []
        if hasattr(self.ui, 'dataDirsList'):
            for i in range(self.ui.dataDirsList.count()):
                item = self.ui.dataDirsList.item(i)
                data = item.data(Qt.UserRole)
                items.append(data)
        return items
    
    def set_data_dirs(self, items):
        """Set the data directory items."""
        if hasattr(self.ui, 'dataDirsList'):
            self.ui.dataDirsList.clear()
            if items:
                for item in items:
                    if isinstance(item, dict) and "source" in item and "target" in item:
                        source = item["source"]
                        target = item["target"]
                        item_text = f"{source} -> {target}"
                        list_item = QListWidgetItem(item_text)
                        list_item.setData(Qt.UserRole, {"source": source, "target": target})
                        self.ui.dataDirsList.addItem(list_item)
    
    def browse_file(self, line_edit, file_filter):
        """Browse for a file and set it in the line edit."""
        file_path, _ = QFileDialog.getOpenFileName(self.ui, "Select File", "", file_filter)
        if file_path:
            line_edit.setText(file_path)
    
    def browse_directory(self, line_edit):
        """Browse for a directory and set it in the line edit."""
        dir_path = QFileDialog.getExistingDirectory(self.ui, "Select Directory")
        if dir_path:
            line_edit.setText(dir_path)
    
    def generate_uuid(self):
        """Generate a UUID for the installer upgrade code."""
        self.ui.installerUpgradeCode.setText(str(uuid.uuid4()))
    
    def add_list_item(self, list_widget):
        """Add a new item to the list widget."""
        text, ok = QInputDialog.getText(self.ui, "Add Item", "Enter value:")
        if ok and text:
            list_widget.addItem(text)
    
    def edit_list_item(self, list_widget):
        """Edit the selected item in the list widget."""
        current_item = list_widget.currentItem()
        if current_item:
            text, ok = QInputDialog.getText(self.ui, "Edit Item", "Edit value:", 
                                          text=current_item.text())
            if ok and text:
                current_item.setText(text)
    
    def remove_list_item(self, list_widget):
        """Remove the selected item from the list widget."""
        current_row = list_widget.currentRow()
        if current_row >= 0:
            list_widget.takeItem(current_row)
    
    def get_list_items(self, list_widget):
        """Get all items from the list widget."""
        items = []
        for i in range(list_widget.count()):
            items.append(list_widget.item(i).text())
        return items
    
    def set_list_items(self, list_widget, items):
        """Set the items in the list widget."""
        list_widget.clear()
        if items:
            for item in items:
                if item:  # Only add non-empty items
                    list_widget.addItem(str(item))
    
    def load_config(self):
        """Load the configuration from the YAML file."""
        try:
            if not os.path.exists(self.config_file):
                return
                
            with open(self.config_file, 'r') as f:
                self.config_data = yaml.safe_load(f) or {}
            
            # Load project section
            project = self.config_data.get('project', {})
            self.ui.projectName.setText(project.get('name', ''))
            self.ui.projectVersion.setText(project.get('version', ''))
            self.ui.projectDescription.setText(project.get('description', ''))
            self.ui.projectCompany.setText(project.get('company', ''))
            self.ui.projectIcon.setText(project.get('icon', ''))
            self.ui.projectMainFile.setText(project.get('main_file', ''))
            
            # Load build section
            build = self.config_data.get('build', {})
            
            # Output
            output = build.get('output', {})
            self.ui.buildOutputDir.setText(output.get('directory', ''))
            self.ui.buildOutputFilename.setText(output.get('filename', ''))
            
            # Options
            options = build.get('options', {})
            self.ui.buildStandalone.setChecked(options.get('standalone', False))
            self.ui.buildOnefile.setChecked(options.get('onefile', False))
            self.ui.buildRemoveOutput.setChecked(options.get('remove_output', False))
            self.ui.buildSplashScreen.setText(options.get('splash_screen', ''))
            
            # Include
            include = build.get('include', {})
            
            # Packages
            self.set_list_items(self.ui.packagesList, include.get('packages', []))
            
            # Plugins
            self.set_list_items(self.ui.pluginsList, include.get('plugins', []))
            
            # Data directories
            self.set_data_dirs(include.get('data_dirs', []))
            
            # External data
            if hasattr(self.ui, 'externalDataList'):
                self.set_list_items(self.ui.externalDataList, include.get('external_data', []))
            
            # Files
            if hasattr(self.ui, 'filesList'):
                self.set_list_items(self.ui.filesList, include.get('files', []))
            
            # Copy beside
            if hasattr(self.ui, 'copyBesideList'):
                copy_beside = build.get('copy_beside', [])
                self.set_copy_beside_items(copy_beside)
            elif hasattr(self.ui, 'copyBesideEnabled'):
                # If we have the checkbox but not the list, just enable it if there are items
                copy_beside = build.get('copy_beside', [])
                self.ui.copyBesideEnabled.setChecked(bool(copy_beside))
            
            # Installer section
            installer = self.config_data.get('installer', {})
            self.ui.installerEnabled.setChecked(installer.get('enabled', False))
            
            # Output
            installer_output = installer.get('output', {})
            self.ui.installerOutputDir.setText(installer_output.get('directory', ''))
            self.ui.installerOutputFilename.setText(installer_output.get('filename', ''))
            
            # Metadata
            metadata = installer.get('metadata', {})
            self.ui.installerManufacturer.setText(metadata.get('manufacturer', ''))
            self.ui.installerProductName.setText(metadata.get('product_name', ''))
            self.ui.installerUpgradeCode.setText(metadata.get('upgrade_code', ''))
            
            # UI
            ui = installer.get('ui', {})
            self.ui.installerBannerImage.setText(ui.get('banner_image', ''))
            self.ui.installerDialogImage.setText(ui.get('dialog_image', ''))
            
            # License
            self.ui.installerLicenseFile.setText(installer.get('license_file', ''))
            
            # Shortcuts
            shortcuts = installer.get('shortcuts', {})
            self.ui.installerDesktopShortcut.setChecked(shortcuts.get('desktop', False))
            self.ui.installerStartMenuShortcut.setChecked(shortcuts.get('start_menu', False))
            
            # Debug section
            debug = self.config_data.get('debug', {})
            self.ui.debugEnabled.setChecked(debug.get('enabled', False))
            
            # Console
            console = debug.get('console', {})
            console_mode = console.get('mode', 'disabled')
            index = self.ui.debugConsoleMode.findText(console_mode)
            if index >= 0:
                self.ui.debugConsoleMode.setCurrentIndex(index)
            
            # Load exclude section
            self.set_list_items(self.ui.excludeList, self.config_data.get('exclude', []))
            
        except Exception as e:
            QMessageBox.critical(self.ui, "Error", f"Failed to load configuration: {str(e)}")
            import traceback
            print(f"Exception details: {traceback.format_exc()}")
    
    def save_config(self):
        """Save the configuration to the YAML file."""
        try:
            # Build the configuration dictionary
            config = {}
            
            # Project section
            config['project'] = {
                'name': self.ui.projectName.text(),
                'version': self.ui.projectVersion.text(),
                'description': self.ui.projectDescription.text(),
                'company': self.ui.projectCompany.text(),
                'icon': self.ui.projectIcon.text(),
                'main_file': self.ui.projectMainFile.text()
            }
            
            # Build section
            build = {}
            
            # Output
            build['output'] = {
                'directory': self.ui.buildOutputDir.text(),
                'filename': self.ui.buildOutputFilename.text()
            }
            
            # Options
            build['options'] = {
                'standalone': self.ui.buildStandalone.isChecked(),
                'onefile': self.ui.buildOnefile.isChecked(),
                'splash_screen': self.ui.buildSplashScreen.text(),
                'remove_output': self.ui.buildRemoveOutput.isChecked()
            }
            
            # Include
            include = {}
            
            # Packages
            include['packages'] = self.get_list_items(self.ui.packagesList)
            
            # Plugins
            include['plugins'] = self.get_list_items(self.ui.pluginsList)
            
            # Data directories
            include['data_dirs'] = self.get_data_dirs()
            
            # External data
            if hasattr(self.ui, 'externalDataList'):
                include['external_data'] = self.get_list_items(self.ui.externalDataList)
            
            # Files
            if hasattr(self.ui, 'filesList'):
                include['files'] = self.get_list_items(self.ui.filesList)
            
            build['include'] = include
            
            # Copy beside
            if hasattr(self.ui, 'copyBesideList'):
                copy_beside = self.get_copy_beside_items()
                build['copy_beside'] = copy_beside
            elif hasattr(self.ui, 'copyBesideEnabled') and self.ui.copyBesideEnabled.isChecked():
                # If we only have the checkbox, preserve the existing copy_beside list
                copy_beside = self.config_data.get('build', {}).get('copy_beside', [])
                build['copy_beside'] = copy_beside
            
            config['build'] = build
            
            # Installer section
            installer = {}
            installer['enabled'] = self.ui.installerEnabled.isChecked()
            
            # Output
            installer['output'] = {
                'directory': self.ui.installerOutputDir.text(),
                'filename': self.ui.installerOutputFilename.text()
            }
            
            # Metadata
            installer['metadata'] = {
                'manufacturer': self.ui.installerManufacturer.text(),
                'product_name': self.ui.installerProductName.text(),
                'upgrade_code': self.ui.installerUpgradeCode.text()
            }
            
            # UI
            installer['ui'] = {
                'banner_image': self.ui.installerBannerImage.text(),
                'dialog_image': self.ui.installerDialogImage.text()
            }
            
            # License
            installer['license_file'] = self.ui.installerLicenseFile.text()
            
            # Shortcuts
            installer['shortcuts'] = {
                'desktop': self.ui.installerDesktopShortcut.isChecked(),
                'start_menu': self.ui.installerStartMenuShortcut.isChecked()
            }
            
            config['installer'] = installer
            
            # Debug section
            debug = {}
            debug['enabled'] = self.ui.debugEnabled.isChecked()
            
            # Console
            debug['console'] = {
                'mode': self.ui.debugConsoleMode.currentText(),
                'stdout': None,
                'stderr': None
            }
            
            config['debug'] = debug
            
            # Exclude section
            config['exclude'] = self.get_list_items(self.ui.excludeList)
            
            # Save to file
            with open(self.config_file, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            
            print(f"Configuration saved to {self.config_file}")
            QMessageBox.information(self.ui, "Success", f"Configuration saved to {self.config_file}")
            
        except Exception as e:
            QMessageBox.critical(self.ui, "Error", f"Failed to save configuration: {str(e)}")
            import traceback
            print(f"Exception details: {traceback.format_exc()}")
    
    def browse_config(self):
        """Browse for a configuration file to load."""
        file_path, _ = QFileDialog.getOpenFileName(
            self.ui, "Select Configuration File", "", "YAML Files (*.yaml *.yml)"
        )
        if file_path:
            self.config_file = file_path
            self.load_config()
    
    def run_command(self, command, args=None):
        """Run a command and display output in the UI
        
        Args:
            command: Command to run (e.g., 'build-exe', 'build-installer')
            args: Optional arguments for the command
        """
        if not hasattr(self, 'config_file') or not self.config_file:
            QMessageBox.warning(self.ui, "Error", "Please save the configuration file first.")
            return
            
        # Save the configuration first
        self.save_config()
            
        # Create a progress dialog
        self.progress_dialog = ProgressDialog(self.ui)
        self.progress_dialog.setWindowTitle(f"Running {command}")
        icon_path = os.path.join(os.path.dirname(__file__), "installer_creator_logo.png")
        self.progress_dialog.setWindowIcon(QIcon(icon_path))
        self.progress_dialog.show()
        self.progress_dialog.update_status("Starting...")
        
        # Connect the cancel button to our cancel_build method
        self.progress_dialog.cancel_button.clicked.connect(self.cancel_build)
        
        # Create signals to update UI from thread
        signals = CommandSignals()
        signals.output.connect(self.append_output)
        signals.progress.connect(self.update_progress)
        signals.status.connect(self.update_status)
        signals.finished.connect(self.command_finished)
        
        # Create a thread to run the command
        thread = threading.Thread(target=self._run_command_thread, args=(command, args, signals))
        thread.daemon = True
        thread.start()
    
    def update_status(self, message):
        """Update the status message in the progress dialog"""
        if hasattr(self, 'progress_dialog') and self.progress_dialog:
            self.progress_dialog.update_status(message)
    
    def _run_command_thread(self, command, args=None, signals=None):
        """Thread to run a command and update the UI
        
        Args:
            command: Command to run (e.g., 'build-exe', 'build-installer')
            args: Optional arguments for the command
            signals: CommandSignals object for UI updates
        """
        try:
            # Clear output
            signals.output.emit("Starting build process...\n")
            
            # Get Python executable
            python_exe = sys.executable
            
            # Get config file path
            config_file_path = self.config_file
            
            # Get project directory
            project_dir = os.path.dirname(config_file_path)
            
            # Set environment variables to force plain progress output
            env = os.environ.copy()
            env['NUITKA_PLAIN_PROGRESS'] = '1'
            
            # Define progress markers
            progress_markers = {
                "Nuitka-Options:": 30,
                "Starting Python compilation": 35,
                "C compiler": 40,
                "Compiling": 45,
                "Generating": 50,
                "Building extension modules": 55,
                "Linking": 60,
                "Packaging": 75,
                "Copying": 85,
                "Executable built successfully": 95
            }
            
            # Create command
            if command == "build-exe":
                cmd = [python_exe, "-m", "installer_creator.build_exe", config_file_path]
                if args:
                    cmd.extend(args)
                signals.output.emit(f"Running command: {' '.join(cmd)}\n")
                
                # Get output directory from config
                try:
                    with open(config_file_path, 'r') as f:
                        config = yaml.safe_load(f)
                    
                    output_dir = config.get('build', {}).get('output', {}).get('directory', 'dist')
                    
                    if not os.path.isabs(output_dir):
                        output_dir = os.path.join(project_dir, output_dir)
                except Exception as e:
                    signals.output.emit(f"Error reading config file: {e}\n")
                    output_dir = os.path.join(project_dir, 'dist')
                
                output_dir = os.path.normpath(output_dir)
                
                # Check if output directory exists
                if not os.path.exists(output_dir):
                    try:
                        signals.output.emit(f"Creating output directory: {output_dir}\n")
                        os.makedirs(output_dir, exist_ok=True)
                        signals.output.emit(f"Created output directory: {output_dir}\n")
                    except Exception as e:
                        signals.output.emit(f"Error creating output directory: {e}\n")
                        signals.output.emit(f"Debug: Exception details: {type(e).__name__}: {str(e)}\n")
                
                # Check if we have write permission to the output directory
                if os.path.exists(output_dir):
                    if os.access(output_dir, os.W_OK):
                        signals.output.emit(f"Output directory is writable: {output_dir}\n")
                    else:
                        signals.output.emit(f"Warning: Output directory is not writable: {output_dir}\n")
                        signals.output.emit("You may need to run as administrator or change the output directory.\n")
                
                # Run the command
                try:
                    # Use winpty for Windows terminal handling if available
                    if sys.platform == 'win32' and 'winpty' in sys.modules and winpty is not None:
                        signals.output.emit("Using winpty for Windows terminal handling...\n")
                        
                        # Convert command list to a single string for display
                        cmd_str = ' '.join(cmd)
                        signals.output.emit(f"Executing command: {cmd_str}\n")
                        
                        # Create a winpty terminal
                        term = winpty.PtyProcess.spawn(cmd, cwd=project_dir, env=env)
                        self.active_process = term
                        
                        # Process output in real-time
                        line_count = 0
                        last_progress = 10
                        
                        # Read output in a loop
                        while True:
                            try:
                                # Check if process was cancelled
                                if not hasattr(self, 'active_process') or self.active_process is None:
                                    signals.output.emit("Build process was cancelled.\n")
                                    break
                                
                                # Read output with timeout
                                output = term.read()
                                if output:
                                    # Process each line
                                    for line in output.splitlines(True):  # Keep line endings
                                        # Clean the line from terminal control characters
                                        clean_line = re.sub(r'\x1b\[\d+[a-zA-Z]', '', line)
                                        
                                        # Send output to UI
                                        signals.output.emit(clean_line)
                                        line_count += 1
                                        
                                        # Check for explicit progress markers
                                        if clean_line.strip().startswith("PROGRESS:"):
                                            try:
                                                parts = clean_line.strip().split(":", 2)
                                                if len(parts) >= 2:
                                                    progress = int(parts[1])
                                                    message = parts[2].strip() if len(parts) > 2 else ""
                                                    signals.progress.emit(progress)
                                                    last_progress = progress
                                                    if message:
                                                        signals.status.emit(message)
                                                continue
                                            except (ValueError, IndexError):
                                                pass
                                        
                                        # Update progress based on output content
                                        for marker, progress in progress_markers.items():
                                            if marker in clean_line and progress > last_progress:
                                                signals.progress.emit(progress)
                                                last_progress = progress
                                                # Update status message based on the marker
                                                if "Nuitka-Options:" in clean_line:
                                                    signals.status.emit("Configuring Nuitka options...")
                                                elif "Starting Python compilation" in clean_line:
                                                    signals.status.emit("Starting Python compilation...")
                                                elif "C compiler" in clean_line:
                                                    signals.status.emit("Setting up C compiler...")
                                                elif "Compiling" in clean_line:
                                                    signals.status.emit("Compiling Python modules...")
                                                elif "Generating" in clean_line:
                                                    signals.status.emit("Generating C code...")
                                                elif "Building extension modules" in clean_line:
                                                    signals.status.emit("Building extension modules...")
                                                elif "Linking" in clean_line:
                                                    signals.status.emit("Linking modules...")
                                                elif "Packaging" in clean_line:
                                                    signals.status.emit("Packaging application...")
                                                elif "Copying" in clean_line:
                                                    signals.status.emit("Copying dependencies...")
                                                elif "Executable built successfully" in clean_line:
                                                    signals.status.emit("Build completed successfully!")
                                                break
                                
                                # Check if process is still running
                                if not term.isalive():
                                    break
                                    
                            except Exception as e:
                                signals.output.emit(f"Error reading output: {e}\n")
                                break
                        
                        # Wait for process to finish
                        return_code = term.wait()
                        
                        # Clear the active process
                        self.active_process = None
                        
                        # Check return code
                        if return_code != 0:
                            signals.output.emit(f"Command failed with return code {return_code}\n")
                        else:
                            signals.output.emit("Command completed successfully\n")
                            signals.progress.emit(100)
                            signals.status.emit("Build completed successfully!")
                    else:
                        # Fallback to subprocess for non-Windows or if winpty is not available
                        signals.output.emit(f"Using standard subprocess on {sys.platform}...\n")
                        
                        # Convert command list to string for display
                        cmd_str = ' '.join(f'"{arg}"' if ' ' in str(arg) or '\\' in str(arg) else str(arg) for arg in cmd)
                        signals.output.emit(f"Executing command: {cmd_str}\n")
                        
                        # Create process with platform-specific settings
                        if sys.platform == 'win32':
                            process = subprocess.Popen(
                                cmd_str,
                                shell=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                bufsize=1,
                                universal_newlines=True,
                                cwd=project_dir,
                                env=env,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                            )
                        else:
                            # Linux/Unix process creation
                            process = subprocess.Popen(
                                cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                bufsize=1,
                                universal_newlines=True,
                                cwd=project_dir,
                                env=env,
                                preexec_fn=os.setsid  # Use process group for Linux
                            )
                        
                        # Store the active process
                        self.active_process = process
                        
                        # Process output in real-time
                        line_count = 0
                        last_progress = 10
                        
                        # Process output in real-time
                        for line in iter(process.stdout.readline, ''):
                            # Check if process was cancelled
                            if not hasattr(self, 'active_process') or self.active_process is None:
                                signals.output.emit("Build process was cancelled.\n")
                                break
                            
                            # Clean the line from terminal control characters
                            clean_line = re.sub(r'\x1b\[\d+[a-zA-Z]', '', line)
                            
                            # Send output to UI
                            signals.output.emit(clean_line)
                            line_count += 1
                            
                            # Check for explicit progress markers
                            if clean_line.strip().startswith("PROGRESS:"):
                                try:
                                    parts = clean_line.strip().split(":", 2)
                                    if len(parts) >= 2:
                                        progress = int(parts[1])
                                        message = parts[2].strip() if len(parts) > 2 else ""
                                        signals.progress.emit(progress)
                                        last_progress = progress
                                        if message:
                                            signals.status.emit(message)
                                    continue
                                except (ValueError, IndexError):
                                    pass
                            
                            # Update progress based on output content
                            for marker, progress in progress_markers.items():
                                if marker in clean_line and progress > last_progress:
                                    signals.progress.emit(progress)
                                    last_progress = progress
                                    # Update status message based on the marker
                                    if "Nuitka-Options:" in clean_line:
                                        signals.status.emit("Configuring Nuitka options...")
                                    elif "Starting Python compilation" in clean_line:
                                        signals.status.emit("Starting Python compilation...")
                                    elif "C compiler" in clean_line:
                                        signals.status.emit("Setting up C compiler...")
                                    elif "Compiling" in clean_line:
                                        signals.status.emit("Compiling Python modules...")
                                    elif "Generating" in clean_line:
                                        signals.status.emit("Generating C code...")
                                    elif "Building extension modules" in clean_line:
                                        signals.status.emit("Building extension modules...")
                                    elif "Linking" in clean_line:
                                        signals.status.emit("Linking modules...")
                                    elif "Packaging" in clean_line:
                                        signals.status.emit("Packaging application...")
                                    elif "Copying" in clean_line:
                                        signals.status.emit("Copying dependencies...")
                                    elif "Executable built successfully" in clean_line:
                                        signals.status.emit("Build completed successfully!")
                                    break
                        
                        # Wait for process to finish
                        return_code = process.wait()
                        
                        # Clear the active process
                        self.active_process = None
                        
                        # Check return code
                        if return_code != 0:
                            signals.output.emit(f"Command failed with return code {return_code}\n")
                        else:
                            signals.output.emit("Command completed successfully\n")
                            signals.progress.emit(100)
                            signals.status.emit("Build completed successfully!")
                    
                    # Signal that the command is finished
                    signals.finished.emit(0 if return_code == 0 else 1)
                    
                except Exception as e:
                    signals.output.emit(f"Error running command: {e}\n")
                    import traceback
                    signals.output.emit(f"Exception details: {traceback.format_exc()}\n")
                    
                    # Clear the active process
                    if hasattr(self, 'active_process'):
                        self.active_process = None
                    
                    # Signal that the command is finished
                    signals.finished.emit(1)
            else:
                signals.output.emit(f"Unknown command: {command}\n")
                signals.finished.emit(1)
        except Exception as e:
            signals.output.emit(f"Error running command: {e}\n")
            import traceback
            signals.output.emit(f"Exception details: {traceback.format_exc()}\n")
            # Clear the active process reference
            if hasattr(self, 'active_process'):
                self.active_process = None
            # Signal that the command is finished
            signals.finished.emit(1)
        finally:
            # Clear the active process reference
            if hasattr(self, 'active_process'):
                self.active_process = None
            # Reset the building flag
            self.is_building = False
    
    def append_output(self, text):
        """Append text to the output text area."""
        self.ui.outputTextEdit.append(text)
        # Scroll to the bottom
        scrollbar = self.ui.outputTextEdit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def command_finished(self, return_code):
        """Handle command completion."""
        self.ui.outputTextEdit.append("\n" + "=" * 50)
        if return_code == 0:
            self.ui.outputTextEdit.append("\nCommand completed successfully.")
        else:
            self.ui.outputTextEdit.append(f"\nCommand failed with return code {return_code}.")
        
        # Close the progress dialog if it exists
        if hasattr(self, 'progress_dialog') and self.progress_dialog:
            self.progress_dialog.accept()
            
        # Clear the active process reference
        self.active_process = None
        
        # Reset the building flag
        self.is_building = False
    
    def update_progress(self, value):
        """Update the progress bar."""
        self.progress_dialog.update_progress(value)
    
    def update_status(self, text):
        """Update the status text."""
        self.progress_dialog.update_status(text)
    
    def build_executable(self):
        """Build the executable using the current configuration."""
        if self.is_building:
            self.ui.outputTextEdit.append("\nBuild already in progress. Please wait for it to finish.\n")
            return
        
        self.is_building = True
        self.run_command("build-exe")
    
    def build_installer(self):
        """Build the installer using the current configuration."""
        if self.is_building:
            self.ui.outputTextEdit.append("\nBuild already in progress. Please wait for it to finish.\n")
            return
        
        self.is_building = True
        self.run_command("build-installer")
    
    def clear_output(self):
        """Clear the output text area."""
        self.ui.outputTextEdit.clear()
    
    def add_file_or_dir_item(self, list_widget):
        """Add a new file or directory item to the list widget."""
        # Create a menu with options
        menu = QMenu(self.ui)
        file_action = QAction("Add File", menu)
        dir_action = QAction("Add Directory", menu)
        
        menu.addAction(file_action)
        menu.addAction(dir_action)
        
        # Show the menu at the cursor position
        button = self.sender()
        if isinstance(button, QPushButton):
            # Show the menu at the button's position
            cursor_pos = button.mapToGlobal(button.rect().bottomLeft())
        else:
            # Fallback to mouse cursor position
            from PySide6.QtGui import QCursor
            cursor_pos = QCursor.pos()
            
        action = menu.exec(cursor_pos)
        
        if action == file_action:
            # Browse for a file
            file_path, _ = QFileDialog.getOpenFileName(self.ui, "Select File")
            if file_path:
                # Convert to relative path if possible
                try:
                    project_dir = os.path.dirname(self.config_file)
                    rel_path = os.path.relpath(file_path, project_dir)
                    # If the relative path starts with "..", use the absolute path
                    if rel_path.startswith(".."):
                        item_path = file_path
                    else:
                        item_path = rel_path
                except ValueError:
                    # Different drives, use absolute path
                    item_path = file_path
                    
                # Add the file to the list
                item = QListWidgetItem(f"File: {os.path.basename(file_path)}")
                item.setData(Qt.UserRole, {"type": "file", "path": item_path})
                list_widget.addItem(item)
                self.config_modified = True
        elif action == dir_action:
            # Browse for a directory
            dir_path = QFileDialog.getExistingDirectory(self.ui, "Select Directory")
            if dir_path:
                # Convert to relative path if possible
                try:
                    project_dir = os.path.dirname(self.config_file)
                    rel_path = os.path.relpath(dir_path, project_dir)
                    # If the relative path starts with "..", use the absolute path
                    if rel_path.startswith(".."):
                        item_path = dir_path
                    else:
                        item_path = rel_path
                except ValueError:
                    # Different drives, use absolute path
                    item_path = dir_path
                    
                # Add the directory to the list
                item = QListWidgetItem(f"Directory: {os.path.basename(dir_path)}")
                item.setData(Qt.UserRole, {"type": "directory", "path": item_path})
                list_widget.addItem(item)
                self.config_modified = True
    
    def edit_file_or_dir_item(self, list_widget):
        """Edit the selected file or directory item in the list widget."""
        # Get the selected item
        current_item = list_widget.currentItem()
        if not current_item:
            return
        
        # Get the item data
        item_path = current_item.data(Qt.UserRole)
        
        # Check if it's a file or directory
        is_file = item_path["type"] == "file"
        
        # Get the absolute path for file dialog
        abs_path = item_path["path"]
        if not os.path.isabs(abs_path):
            project_dir = os.path.dirname(self.config_file)
            abs_path = os.path.join(project_dir, abs_path)
        
        if is_file:
            # Browse for a file
            file_path, _ = QFileDialog.getOpenFileName(self.ui, "Select File", abs_path)
            if file_path:
                # Get the relative path if possible
                try:
                    project_dir = os.path.dirname(self.config_file)
                    rel_path = os.path.relpath(file_path, project_dir)
                    # If the relative path starts with "..", use the absolute path
                    if rel_path.startswith(".."):
                        item_path["path"] = file_path
                    else:
                        item_path["path"] = rel_path
                except ValueError:
                    # Different drives, use absolute path
                    item_path["path"] = file_path
                
                # Update the item
                current_item.setText(f"File: {os.path.basename(file_path)}")
                current_item.setData(Qt.UserRole, item_path)
                self.config_modified = True
        else:
            # Browse for a directory
            dir_path = QFileDialog.getExistingDirectory(self.ui, "Select Directory", abs_path)
            if dir_path:
                # Get the relative path if possible
                try:
                    project_dir = os.path.dirname(self.config_file)
                    rel_path = os.path.relpath(dir_path, project_dir)
                    # If the relative path starts with "..", use the absolute path
                    if rel_path.startswith(".."):
                        item_path["path"] = dir_path
                    else:
                        item_path["path"] = rel_path
                except ValueError:
                    # Different drives, use absolute path
                    item_path["path"] = dir_path
                
                # Update the item
                current_item.setText(f"Directory: {os.path.basename(dir_path)}")
                current_item.setData(Qt.UserRole, item_path)
                self.config_modified = True
    
    def get_copy_beside_items(self):
        """Get the list of copy_beside items."""
        items = []
        for i in range(self.ui.copyBesideList.count()):
            item = self.ui.copyBesideList.item(i)
            # Get the actual path data, not the display text
            item_data = item.data(Qt.UserRole)
            items.append(item_data["path"])
        return items
    
    def set_copy_beside_items(self, items):
        """Set the list of copy_beside items."""
        # Clear the list first
        self.ui.copyBesideList.clear()
        
        # Add each item to the list
        for item_path in items:
            # Determine if it's a file or directory
            full_path = item_path
            if not os.path.isabs(item_path):
                project_dir = os.path.dirname(self.config_file)
                full_path = os.path.join(project_dir, item_path)
            
            if os.path.isfile(full_path):
                item_type = "File"
            elif os.path.isdir(full_path):
                item_type = "Directory"
            else:
                item_type = "Unknown"
                
            item = QListWidgetItem(f"{item_type}: {os.path.basename(item_path)}")
            item.setData(Qt.UserRole, {"type": item_type.lower(), "path": item_path})
            self.ui.copyBesideList.addItem(item)
    
    def cancel_build(self):
        """Cancel the current build process."""
        print("Canceling build...")
        if hasattr(self, 'active_process') and self.active_process is not None:
            print("Cancelling active process...")
            try:
                # Check if the active process is a winpty process
                if hasattr(self.active_process, 'terminate') and callable(getattr(self.active_process, 'terminate')) and 'winpty' in sys.modules and winpty is not None:
                    # For winpty processes
                    self.append_output("Terminating winpty process...\n")
                    self.active_process.terminate(force=True)
                elif hasattr(self.active_process, 'kill') and callable(getattr(self.active_process, 'kill')):
                    # For other process types with kill method
                    self.append_output("Killing process...\n")
                    self.active_process.kill()
                else:
                    # For subprocess processes
                    self.append_output("Terminating subprocess...\n")
                    if sys.platform == 'win32':
                        # On Windows, we need to terminate the process group
                        import signal
                        try:
                            # Try to terminate the process group
                            os.kill(self.active_process.pid, signal.CTRL_BREAK_EVENT)
                        except:
                            # Fallback to terminate
                            self.active_process.terminate()
                    else:
                        # On Linux/Unix, kill the process group
                        try:
                            # Get process group ID and kill it
                            import signal
                            os.killpg(os.getpgid(self.active_process.pid), signal.SIGTERM)
                            self.append_output("Sent SIGTERM to process group\n")
                            
                            # Give it a moment to terminate gracefully
                            import time
                            time.sleep(0.5)
                            
                            # If still running, force kill
                            if self.active_process.poll() is None:
                                os.killpg(os.getpgid(self.active_process.pid), signal.SIGKILL)
                                self.append_output("Sent SIGKILL to process group\n")
                        except Exception as e:
                            # Fallback to terminate if process group handling fails
                            self.append_output(f"Error killing process group: {e}\n")
                            self.active_process.terminate()
                
                # Clear the active process reference
                self.active_process = None
                
                # Update the UI
                if hasattr(self, 'progress_dialog') and self.progress_dialog:
                    self.progress_dialog.update_status("Build cancelled")
                    self.progress_dialog.close()
                
                # Append to output
                self.append_output("Build process was cancelled by user.\n")
                
            except Exception as e:
                self.append_output(f"Error cancelling build: {e}\n")
                # Clear the active process reference
                self.active_process = None


def main(config_file=None):
    """Run the configuration editor."""
    app = QApplication(sys.argv)
    
    # If config_file is not provided, check command line arguments
    if config_file is None and len(sys.argv) > 1:
        config_file = sys.argv[1]
    
    # Ensure config_file is an absolute path
    if config_file and not os.path.isabs(config_file):
        config_file = os.path.abspath(config_file)
        print(f"Using config file: {config_file}")
    
    window = ConfigEditorWindow(config_file)
    
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
