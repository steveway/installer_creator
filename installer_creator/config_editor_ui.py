#!/usr/bin/env python3
"""
Configuration Editor UI for installer-creator

This module provides a GUI for editing build_config.yaml files using Qt Designer UI.
"""
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from queue import Empty
from typing import Any, Dict, List, Optional, Union

import yaml
from PySide6.QtCore import QFile, QIODevice, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QIcon, QTextCursor
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Import pywinpty for Windows
if sys.platform == "win32":
    try:
        import winpty
    except ImportError:
        print(
            "Warning: pywinpty not installed. Windows terminal handling may be limited."
        )
        winpty = None
else:
    # On non-Windows platforms, set winpty to None
    winpty = None


class CommandSignals(QObject):
    """Signals for command execution with dual progress support"""

    output = Signal(str)
    overall_progress = Signal(int)
    overall_status = Signal(str)
    task_progress = Signal(int)
    task_status = Signal(str)
    finished = Signal(int)
    
    # Legacy signals for backward compatibility
    progress = Signal(int)
    status = Signal(str)


class ProgressDialog(QDialog):
    """Dialog for showing build progress with dual progress bars."""

    def __init__(self, parent=None, title="Building..."):
        super().__init__(parent)

        self.setWindowTitle(title)
        self.setMinimumWidth(500)
        self.setMinimumHeight(200)

        layout = QVBoxLayout(self)

        # Overall status and progress
        self.overall_status_label = QLabel("Initializing...")
        self.overall_status_label.setStyleSheet("font-weight: bold; color: #2c3e50;")
        layout.addWidget(self.overall_status_label)

        self.overall_progress_bar = QProgressBar()
        self.overall_progress_bar.setRange(0, 100)
        self.overall_progress_bar.setValue(0)
        self.overall_progress_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid #3498db;
                border-radius: 5px;
                text-align: center;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #3498db;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.overall_progress_bar)

        # Add some spacing
        layout.addSpacing(10)

        # Current task status and progress
        self.task_status_label = QLabel("Waiting for task...")
        self.task_status_label.setStyleSheet("color: #7f8c8d;")
        layout.addWidget(self.task_status_label)

        self.task_progress_bar = QProgressBar()
        self.task_progress_bar.setRange(0, 100)
        self.task_progress_bar.setValue(0)
        self.task_progress_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid #27ae60;
                border-radius: 5px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #27ae60;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.task_progress_bar)

        # Add some spacing
        layout.addSpacing(15)

        # Add a cancel button
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #c0392b;
            }
            QPushButton:pressed {
                background-color: #a93226;
            }
        """)
        layout.addWidget(self.cancel_button)

        # Don't allow closing with X button
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)

    def update_overall_progress(self, value):
        """Update the overall progress bar."""
        self.overall_progress_bar.setValue(value)

    def update_overall_status(self, text):
        """Update the overall status text."""
        self.overall_status_label.setText(text)

    def update_task_progress(self, value):
        """Update the current task progress bar."""
        self.task_progress_bar.setValue(value)

    def update_task_status(self, text):
        """Update the current task status text."""
        self.task_status_label.setText(text)

    # Legacy methods for backward compatibility
    def update_progress(self, value):
        """Update the overall progress bar (legacy method)."""
        self.update_overall_progress(value)

    def update_status(self, text):
        """Update the overall status text (legacy method)."""
        self.update_overall_status(text)


class DataDirDialog(QDialog):
    """Dialog for adding/editing data directory items."""

    def __init__(self, parent=None, source="", target=""):
        super().__init__(parent)

        self.setWindowTitle("Data Directory")

        layout = QFormLayout(self)

        self.source_edit = QLineEdit(source)
        icon_path = os.path.join(
            os.path.dirname(__file__), "installer_creator_logo.png"
        )
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

        self.config_file = config_file or "build_config.yaml"
        self.config_data = {}
        self.active_process = None  # Store the active process for cancellation
        self.is_building = False  # Flag to prevent duplicate builds
        self.config_modified = False  # Track unsaved changes

        # Load UI from file
        ui_file = QFile(os.path.join(os.path.dirname(__file__), "config_editor.ui"))
        if not ui_file.open(QIODevice.ReadOnly):
            raise RuntimeError(
                f"Cannot open {ui_file.fileName()}: {ui_file.errorString()}"
            )

        loader = QUiLoader()
        self.ui = loader.load(ui_file)
        icon_path = os.path.join(
            os.path.dirname(__file__), "installer_creator_logo.png"
        )
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
            lambda: self.browse_file(self.ui.projectIcon, "Icon Files (*.ico)")
        )
        self.ui.mainFileBrowseButton.clicked.connect(
            lambda: self.browse_file(self.ui.projectMainFile, "Python Files (*.py)")
        )
        self.ui.pythonPathBrowseButton.clicked.connect(
            lambda: self.browse_file(self.ui.projectPythonPath, "Python Executable (*.exe)" if sys.platform == "win32" else "Python Executable (*)")
        )
        self.ui.outputDirBrowseButton.clicked.connect(
            lambda: self.browse_directory(self.ui.buildOutputDir)
        )
        self.ui.splashScreenBrowseButton.clicked.connect(
            lambda: self.browse_file(
                self.ui.buildSplashScreen, "Image Files (*.png *.jpg *.bmp)"
            )
        )
        self.ui.installerOutputDirBrowseButton.clicked.connect(
            lambda: self.browse_directory(self.ui.installerOutputDir)
        )
        self.ui.bannerImageBrowseButton.clicked.connect(
            lambda: self.browse_file(self.ui.installerBannerImage, "BMP Files (*.bmp)")
        )
        self.ui.dialogImageBrowseButton.clicked.connect(
            lambda: self.browse_file(self.ui.installerDialogImage, "BMP Files (*.bmp)")
        )
        self.ui.licenseFileBrowseButton.clicked.connect(
            lambda: self.browse_file(self.ui.installerLicenseFile, "RTF Files (*.rtf)")
        )

        # Connect UUID generator
        self.ui.generateUuidButton.clicked.connect(self.generate_uuid)

        # Connect save and load buttons
        self.ui.saveButton.clicked.connect(self.save_config)
        self.ui.loadButton.clicked.connect(self.browse_config)

        # Connect build buttons
        self.ui.buildExeButton.clicked.connect(self.build_executable)
        self.ui.buildInstallerButton.clicked.connect(self.build_installer)
        self.ui.clearOutputButton.clicked.connect(self.clear_output)
        self.ui.openOutputFolderButton.clicked.connect(self.open_output_folder)

        # Set up debug console mode combobox
        self.ui.debugConsoleMode.addItems(["disabled", "enabled", "force"])

        # Set up list widgets
        self.setup_list_widgets()

        # Connect inputs to modification flag
        self.connect_modification_signals()

    def open_output_folder(self):
        """Open the output folder in the file explorer."""
        output_dir = self.ui.buildOutputDir.text()
        if output_dir:
            os.startfile(output_dir)

    def set_modified(self, modified=True):
        """Set the modified state and update the window title."""
        if self.config_modified != modified:
            self.config_modified = modified
            title = f"Config Editor - {os.path.basename(self.config_file)}"
            if modified:
                title += " *"
            self.ui.setWindowTitle(title)

    def set_ui_enabled(self, enabled: bool):
        """Enable or disable UI elements during build process."""
        # Disable build, save, load buttons
        self.ui.buildExeButton.setEnabled(enabled)
        self.ui.buildInstallerButton.setEnabled(enabled)
        self.ui.saveButton.setEnabled(enabled)
        self.ui.loadButton.setEnabled(enabled)

        # Disable all tabs except the 'Build & Run' tab
        # Assuming the Build & Run tab is the last one. Adjust index if necessary.
        build_run_tab_index = self.ui.tabWidget.count() - 1
        for i in range(self.ui.tabWidget.count()):
            if i != build_run_tab_index:
                self.ui.tabWidget.widget(i).setEnabled(enabled)

        # Keep the Build & Run tab itself enabled, but disable specific controls if needed
        # For now, we keep output and clear button enabled on the Build & Run tab.
        # The cancel button is in the separate progress dialog.

        # Optionally, explicitly disable list modification buttons if they aren't inside disabled tabs:
        if hasattr(self.ui, "packagesAddButton"):
            self.ui.packagesAddButton.setEnabled(enabled)
        if hasattr(self.ui, "packagesEditButton"):
            self.ui.packagesEditButton.setEnabled(enabled)
        if hasattr(self.ui, "packagesRemoveButton"):
            self.ui.packagesRemoveButton.setEnabled(enabled)
        # ... repeat for plugins, dataDirs, externalData, files, copyBeside, exclude ...
        if hasattr(self.ui, "pluginsAddButton"):
            self.ui.pluginsAddButton.setEnabled(enabled)
        if hasattr(self.ui, "pluginsEditButton"):
            self.ui.pluginsEditButton.setEnabled(enabled)
        if hasattr(self.ui, "pluginsRemoveButton"):
            self.ui.pluginsRemoveButton.setEnabled(enabled)
        if hasattr(self.ui, "dataDirsAddButton"):
            self.ui.dataDirsAddButton.setEnabled(enabled)
        if hasattr(self.ui, "dataDirsEditButton"):
            self.ui.dataDirsEditButton.setEnabled(enabled)
        if hasattr(self.ui, "dataDirsRemoveButton"):
            self.ui.dataDirsRemoveButton.setEnabled(enabled)
        if hasattr(self.ui, "externalDataAddButton"):
            self.ui.externalDataAddButton.setEnabled(enabled)
        if hasattr(self.ui, "externalDataEditButton"):
            self.ui.externalDataEditButton.setEnabled(enabled)
        if hasattr(self.ui, "externalDataRemoveButton"):
            self.ui.externalDataRemoveButton.setEnabled(enabled)
        if hasattr(self.ui, "filesAddButton"):
            self.ui.filesAddButton.setEnabled(enabled)
        if hasattr(self.ui, "filesEditButton"):
            self.ui.filesEditButton.setEnabled(enabled)
        if hasattr(self.ui, "filesRemoveButton"):
            self.ui.filesRemoveButton.setEnabled(enabled)
        if hasattr(self.ui, "copyBesideAddButton"):
            self.ui.copyBesideAddButton.setEnabled(enabled)
        if hasattr(self.ui, "copyBesideEditButton"):
            self.ui.copyBesideEditButton.setEnabled(enabled)
        if hasattr(self.ui, "copyBesideRemoveButton"):
            self.ui.copyBesideRemoveButton.setEnabled(enabled)
        if hasattr(self.ui, "excludeAddButton"):
            self.ui.excludeAddButton.setEnabled(enabled)
        if hasattr(self.ui, "excludeEditButton"):
            self.ui.excludeEditButton.setEnabled(enabled)
        if hasattr(self.ui, "excludeRemoveButton"):
            self.ui.excludeRemoveButton.setEnabled(enabled)
        # Disable UUID generation button
        if hasattr(self.ui, "generateUuidButton"):
            self.ui.generateUuidButton.setEnabled(enabled)

    def connect_modification_signals(self):
        """Connect signals from various widgets to set the modified flag."""
        # LineEdits
        for widget_name in dir(self.ui):
            widget = getattr(self.ui, widget_name)
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(lambda: self.set_modified(True))
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(lambda: self.set_modified(True))
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(lambda: self.set_modified(True))
            # Add connections for other relevant widgets like QSpinBox, QTextEdit, etc. if needed

    def setup_list_widgets(self):
        """Set up the list widgets for packages, plugins, etc."""
        # Packages
        self.ui.packagesAddButton.clicked.connect(
            lambda: self.add_list_item(self.ui.packagesList)
        )
        self.ui.packagesEditButton.clicked.connect(
            lambda: self.edit_list_item(self.ui.packagesList)
        )
        self.ui.packagesRemoveButton.clicked.connect(
            lambda: self.remove_list_item(self.ui.packagesList)
        )

        # Plugins
        self.ui.pluginsAddButton.clicked.connect(
            lambda: self.add_list_item(self.ui.pluginsList)
        )
        self.ui.pluginsEditButton.clicked.connect(
            lambda: self.edit_list_item(self.ui.pluginsList)
        )
        self.ui.pluginsRemoveButton.clicked.connect(
            lambda: self.remove_list_item(self.ui.pluginsList)
        )

        # Data directories
        self.ui.dataDirsAddButton.clicked.connect(self.add_data_dir)
        self.ui.dataDirsEditButton.clicked.connect(self.edit_data_dir)
        self.ui.dataDirsRemoveButton.clicked.connect(
            lambda: self.remove_list_item(self.ui.dataDirsList)
        )

        # External data
        if hasattr(self.ui, "externalDataAddButton"):
            self.ui.externalDataAddButton.clicked.connect(
                lambda: self.add_list_item(self.ui.externalDataList)
            )
            self.ui.externalDataEditButton.clicked.connect(
                lambda: self.edit_list_item(self.ui.externalDataList)
            )
            self.ui.externalDataRemoveButton.clicked.connect(
                lambda: self.remove_list_item(self.ui.externalDataList)
            )

        # Files
        if hasattr(self.ui, "filesAddButton"):
            self.ui.filesAddButton.clicked.connect(
                lambda: self.add_file_item(self.ui.filesList)
            )
            self.ui.filesEditButton.clicked.connect(
                lambda: self.edit_list_item(self.ui.filesList)
            )
            self.ui.filesRemoveButton.clicked.connect(
                lambda: self.remove_list_item(self.ui.filesList)
            )

        # Copy beside
        if hasattr(self.ui, "copyBesideAddButton"):
            self.ui.copyBesideAddButton.clicked.connect(
                lambda: self.add_file_or_dir_item(self.ui.copyBesideList)
            )
            self.ui.copyBesideEditButton.clicked.connect(
                lambda: self.edit_file_or_dir_item(self.ui.copyBesideList)
            )
            self.ui.copyBesideRemoveButton.clicked.connect(
                lambda: self.remove_list_item(self.ui.copyBesideList)
            )

        # Exclude
        self.ui.excludeAddButton.clicked.connect(
            lambda: self.add_list_item(self.ui.excludeList)
        )
        self.ui.excludeEditButton.clicked.connect(
            lambda: self.edit_list_item(self.ui.excludeList)
        )
        self.ui.excludeRemoveButton.clicked.connect(
            lambda: self.remove_list_item(self.ui.excludeList)
        )

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
                self.set_modified(True)

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
                    current_item.setData(
                        Qt.UserRole, {"source": source, "target": target}
                    )
                    self.set_modified(True)

    def get_data_dirs(self):
        """Get all data directory items."""
        items = []
        if hasattr(self.ui, "dataDirsList"):
            for i in range(self.ui.dataDirsList.count()):
                item = self.ui.dataDirsList.item(i)
                data = item.data(Qt.UserRole)
                items.append(data)
        return items

    def set_data_dirs(self, items):
        """Set the data directory items."""
        if hasattr(self.ui, "dataDirsList"):
            self.ui.dataDirsList.clear()
            if items:
                for item in items:
                    if isinstance(item, dict) and "source" in item and "target" in item:
                        source = item["source"]
                        target = item["target"]
                        item_text = f"{source} -> {target}"
                        list_item = QListWidgetItem(item_text)
                        list_item.setData(
                            Qt.UserRole, {"source": source, "target": target}
                        )
                        self.ui.dataDirsList.addItem(list_item)

    def browse_file(self, line_edit, file_filter):
        """Browse for a file and set it in the line edit."""
        file_path, _ = QFileDialog.getOpenFileName(
            self.ui, "Select File", "", file_filter
        )
        if file_path:
            line_edit.setText(file_path)
            self.set_modified(True)

    def browse_directory(self, line_edit):
        """Browse for a directory and set it in the line edit."""
        dir_path = QFileDialog.getExistingDirectory(self.ui, "Select Directory")
        if dir_path:
            line_edit.setText(dir_path)
            self.set_modified(True)

    def generate_uuid(self):
        """Generate a UUID for the installer upgrade code."""
        self.ui.installerUpgradeCode.setText(str(uuid.uuid4()))
        self.set_modified(True)

    def add_list_item(self, list_widget):
        """Add a new item to the list widget."""
        text, ok = QInputDialog.getText(self.ui, "Add Item", "Enter value:")
        if ok and text:
            list_widget.addItem(text)
            self.set_modified(True)

    def add_file_item(self, list_widget):
        """Add a new file item to the list widget using file dialog."""
        file_path, _ = QFileDialog.getOpenFileName(
            self.ui, "Select File to Include", "", "All Files (*)"
        )
        if file_path:
            list_widget.addItem(file_path)
            self.set_modified(True)

    def edit_list_item(self, list_widget):
        """Edit the selected item in the list widget."""
        current_item = list_widget.currentItem()
        if current_item:
            text, ok = QInputDialog.getText(
                self.ui, "Edit Item", "Edit value:", text=current_item.text()
            )
            if ok and text:
                current_item.setText(text)
                self.set_modified(True)

    def remove_list_item(self, list_widget):
        """Remove the selected item from the list widget."""
        current_row = list_widget.currentRow()
        if current_row >= 0:
            list_widget.takeItem(current_row)
            self.set_modified(True)

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
                    self.set_modified(True)

    def load_config(self):
        """Load the configuration from the YAML file."""
        try:
            if not os.path.exists(self.config_file):
                # If file doesn't exist, treat as new, modified config
                self.set_modified(True)
                return

            with open(self.config_file, "r") as f:
                self.config_data = yaml.safe_load(f) or {}

            # Load project section
            project = self.config_data.get("project", {})
            self.ui.projectName.setText(project.get("name", ""))
            self.ui.projectVersion.setText(project.get("version", ""))
            self.ui.projectDescription.setText(project.get("description", ""))
            self.ui.projectCompany.setText(project.get("company", ""))
            self.ui.projectIcon.setText(project.get("icon", ""))
            self.ui.projectMainFile.setText(project.get("main_file", ""))
            # Load Python path if available
            if hasattr(self.ui, "projectPythonPath"):
                self.ui.projectPythonPath.setText(project.get("python_path", ""))

            # Load build section
            build = self.config_data.get("build", {})

            # Output
            output = build.get("output", {})
            self.ui.buildOutputDir.setText(output.get("directory", ""))
            self.ui.buildOutputFilename.setText(output.get("filename", ""))

            # Options
            options = build.get("options", {})
            self.ui.buildStandalone.setChecked(options.get("standalone", False))
            self.ui.buildOnefile.setChecked(options.get("onefile", False))
            self.ui.buildRemoveOutput.setChecked(options.get("remove_output", False))
            self.ui.buildSplashScreen.setText(options.get("splash_screen", ""))

            # Include
            include = build.get("include", {})

            # Packages
            self.set_list_items(self.ui.packagesList, include.get("packages", []))

            # Plugins
            self.set_list_items(self.ui.pluginsList, include.get("plugins", []))

            # Data directories
            self.set_data_dirs(include.get("data_dirs", []))

            # External data
            if hasattr(self.ui, "externalDataList"):
                self.set_list_items(
                    self.ui.externalDataList, include.get("external_data", [])
                )

            # Files
            if hasattr(self.ui, "filesList"):
                self.set_list_items(self.ui.filesList, include.get("files", []))

            # Copy beside
            if hasattr(self.ui, "copyBesideList"):
                copy_beside = build.get("copy_beside", [])
                self.set_copy_beside_items(copy_beside)
            elif hasattr(self.ui, "copyBesideEnabled"):
                # If we have the checkbox but not the list, just enable it if there are items
                copy_beside = build.get("copy_beside", [])
                self.ui.copyBesideEnabled.setChecked(bool(copy_beside))

            # Installer section
            installer = self.config_data.get("installer", {})
            self.ui.installerEnabled.setChecked(installer.get("enabled", False))

            # Output
            installer_output = installer.get("output", {})
            self.ui.installerOutputDir.setText(installer_output.get("directory", ""))
            self.ui.installerOutputFilename.setText(
                installer_output.get("filename", "")
            )

            # Metadata
            metadata = installer.get("metadata", {})
            self.ui.installerManufacturer.setText(metadata.get("manufacturer", ""))
            self.ui.installerProductName.setText(metadata.get("product_name", ""))
            self.ui.installerUpgradeCode.setText(metadata.get("upgrade_code", ""))

            # UI
            ui = installer.get("ui", {})
            self.ui.installerBannerImage.setText(ui.get("banner_image", ""))
            self.ui.installerDialogImage.setText(ui.get("dialog_image", ""))

            # License
            self.ui.installerLicenseFile.setText(installer.get("license_file", ""))

            # Shortcuts
            shortcuts = installer.get("shortcuts", {})
            self.ui.installerDesktopShortcut.setChecked(shortcuts.get("desktop", False))
            self.ui.installerStartMenuShortcut.setChecked(
                shortcuts.get("start_menu", False)
            )

            # Debug section
            debug = self.config_data.get("debug", {})
            self.ui.debugEnabled.setChecked(debug.get("enabled", False))

            # Console
            console = debug.get("console", {})
            console_mode = console.get("mode", "disabled")
            index = self.ui.debugConsoleMode.findText(console_mode)
            if index >= 0:
                self.ui.debugConsoleMode.setCurrentIndex(index)

            # Set stdout and stderr paths if they exist
            stdout_path = console.get("stdout")
            stderr_path = console.get("stderr")

            if stdout_path:
                self.ui.debugConsoleStdout.setText(stdout_path)
            if stderr_path:
                self.ui.debugConsoleStderr.setText(stderr_path)

            # Load exclude section
            self.set_list_items(
                self.ui.excludeList, self.config_data.get("exclude", [])
            )

            # Reset modified state after loading
            self.set_modified(False)

        except Exception as e:
            QMessageBox.critical(
                self.ui, "Error", f"Failed to load configuration: {str(e)}"
            )
            import traceback

            print(f"Exception details: {traceback.format_exc()}")

    def save_config(self):
        """Save the configuration to the YAML file."""
        try:
            # Build the configuration dictionary
            config = {}

            # Project section
            project_config = {
                "name": self.ui.projectName.text(),
                "version": self.ui.projectVersion.text(),
                "description": self.ui.projectDescription.text(),
                "company": self.ui.projectCompany.text(),
                "icon": self.ui.projectIcon.text(),
                "main_file": self.ui.projectMainFile.text(),
            }
            # Add Python path if available and not empty
            if hasattr(self.ui, "projectPythonPath"):
                python_path = self.ui.projectPythonPath.text().strip()
                if python_path:
                    project_config["python_path"] = python_path
            
            config["project"] = project_config

            # Build section
            build = {}

            # Output
            build["output"] = {
                "directory": self.ui.buildOutputDir.text(),
                "filename": self.ui.buildOutputFilename.text(),
            }

            # Options
            build["options"] = {
                "standalone": self.ui.buildStandalone.isChecked(),
                "onefile": self.ui.buildOnefile.isChecked(),
                "splash_screen": self.ui.buildSplashScreen.text(),
                "remove_output": self.ui.buildRemoveOutput.isChecked(),
            }

            # Include
            include = {}

            # Packages
            include["packages"] = self.get_list_items(self.ui.packagesList)

            # Plugins
            include["plugins"] = self.get_list_items(self.ui.pluginsList)

            # Data directories
            include["data_dirs"] = self.get_data_dirs()

            # External data
            if hasattr(self.ui, "externalDataList"):
                include["external_data"] = self.get_list_items(self.ui.externalDataList)

            # Files
            if hasattr(self.ui, "filesList"):
                include["files"] = self.get_list_items(self.ui.filesList)

            build["include"] = include

            # Copy beside
            if hasattr(self.ui, "copyBesideList"):
                copy_beside = self.get_copy_beside_items()
                build["copy_beside"] = copy_beside
            elif (
                hasattr(self.ui, "copyBesideEnabled")
                and self.ui.copyBesideEnabled.isChecked()
            ):
                # If we only have the checkbox, preserve the existing copy_beside list
                copy_beside = self.config_data.get("build", {}).get("copy_beside", [])
                build["copy_beside"] = copy_beside

            config["build"] = build

            # Installer section
            installer = {}
            installer["enabled"] = self.ui.installerEnabled.isChecked()

            # Output
            installer["output"] = {
                "directory": self.ui.installerOutputDir.text(),
                "filename": self.ui.installerOutputFilename.text(),
            }

            # Metadata
            installer["metadata"] = {
                "manufacturer": self.ui.installerManufacturer.text(),
                "product_name": self.ui.installerProductName.text(),
                "upgrade_code": self.ui.installerUpgradeCode.text(),
            }

            # UI
            installer["ui"] = {
                "banner_image": self.ui.installerBannerImage.text(),
                "dialog_image": self.ui.installerDialogImage.text(),
            }

            # License
            installer["license_file"] = self.ui.installerLicenseFile.text()

            # Shortcuts
            installer["shortcuts"] = {
                "desktop": self.ui.installerDesktopShortcut.isChecked(),
                "start_menu": self.ui.installerStartMenuShortcut.isChecked(),
            }

            config["installer"] = installer

            # Debug section
            debug = {}
            debug["enabled"] = self.ui.debugEnabled.isChecked()

            # Console
            stdout_path = self.ui.debugConsoleStdout.text().strip()
            stderr_path = self.ui.debugConsoleStderr.text().strip()

            debug["console"] = {
                "mode": self.ui.debugConsoleMode.currentText(),
                "stdout": stdout_path if stdout_path else None,
                "stderr": stderr_path if stderr_path else None,
            }

            config["debug"] = debug

            # Exclude section
            config["exclude"] = self.get_list_items(self.ui.excludeList)

            # Save to file
            with open(self.config_file, "w") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)

            print(f"Configuration saved to {self.config_file}")
            QMessageBox.information(
                self.ui, "Success", f"Configuration saved to {self.config_file}"
            )

            # Reset modified state after saving
            self.set_modified(False)

        except Exception as e:
            QMessageBox.critical(
                self.ui, "Error", f"Failed to save configuration: {str(e)}"
            )
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
        if not hasattr(self, "config_file") or not self.config_file:
            QMessageBox.warning(
                self.ui, "Error", "Please save the configuration file first."
            )
            return

        # Save the configuration first
        self.save_config()

        # Create a progress dialog
        self.progress_dialog = ProgressDialog(self.ui)
        self.progress_dialog.setWindowTitle(f"Running {command}")
        icon_path = os.path.join(
            os.path.dirname(__file__), "installer_creator_logo.png"
        )
        self.progress_dialog.setWindowIcon(QIcon(icon_path))
        self.progress_dialog.show()
        self.progress_dialog.update_status("Starting...")

        # Connect the cancel button to our cancel_build method
        self.progress_dialog.cancel_button.clicked.connect(self.cancel_build)

        # Create signals to update UI from thread
        signals = CommandSignals()
        signals.output.connect(self.append_output)
        signals.overall_progress.connect(self.update_overall_progress)
        signals.overall_status.connect(self.update_overall_status)
        signals.task_progress.connect(self.update_task_progress)
        signals.task_status.connect(self.update_task_status)
        signals.finished.connect(self.command_finished)
        
        # Legacy signal connections for backward compatibility
        signals.progress.connect(self.update_progress)
        signals.status.connect(self.update_status)

        # Create a thread to run the command
        thread = threading.Thread(
            target=self._run_command_thread, args=(command, args, signals)
        )
        thread.daemon = True
        thread.start()

    def update_status(self, message):
        """Update the status message in the progress dialog"""
        if hasattr(self, "progress_dialog") and self.progress_dialog:
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
            env["NUITKA_PLAIN_PROGRESS"] = "1"

            # Define overall progress phases and their progress ranges
            overall_phases = {
                "initialization": {"start": 0, "end": 10, "message": "Initializing build process..."},
                "nuitka_setup": {"start": 10, "end": 20, "message": "Setting up Nuitka environment..."},
                "compilation": {"start": 20, "end": 70, "message": "Compiling application..."},
                "packaging": {"start": 70, "end": 90, "message": "Packaging executable..."},
                "finalization": {"start": 90, "end": 100, "message": "Finalizing build..."}
            }
            
            # Define detailed Nuitka progress markers for task-specific progress
            nuitka_markers = {
                "Nuitka-Options:": {"phase": "nuitka_setup", "task_progress": 50, "message": "Configuring Nuitka options..."},
                "Starting Python compilation": {"phase": "compilation", "task_progress": 5, "message": "Starting Python compilation..."},
                "C compiler": {"phase": "compilation", "task_progress": 15, "message": "Setting up C compiler..."},
                "Compiling": {"phase": "compilation", "task_progress": 30, "message": "Compiling Python modules..."},
                "Generating": {"phase": "compilation", "task_progress": 50, "message": "Generating C code..."},
                "Building extension modules": {"phase": "compilation", "task_progress": 70, "message": "Building extension modules..."},
                "Linking": {"phase": "compilation", "task_progress": 85, "message": "Linking modules..."},
                "Packaging": {"phase": "packaging", "task_progress": 30, "message": "Packaging application..."},
                "Copying": {"phase": "packaging", "task_progress": 70, "message": "Copying dependencies..."},
                "Executable built successfully": {"phase": "finalization", "task_progress": 100, "message": "Build completed successfully!"},
                # Additional common Nuitka patterns
                "Nuitka:INFO:": {"phase": "nuitka_setup", "task_progress": 10, "message": "Nuitka initialization..."},
                "Nuitka:WARNING:": {"phase": "compilation", "task_progress": 20, "message": "Processing warnings..."},
                "Used Python": {"phase": "nuitka_setup", "task_progress": 30, "message": "Python environment detected..."},
                "Creating single file": {"phase": "packaging", "task_progress": 50, "message": "Creating single file executable..."},
                "Optimization": {"phase": "compilation", "task_progress": 60, "message": "Optimizing code..."},
                "Backend C compiler": {"phase": "compilation", "task_progress": 25, "message": "Configuring C compiler..."},
                "Total memory usage": {"phase": "finalization", "task_progress": 90, "message": "Finalizing build..."}
            }
            
            # Initialize progress tracking
            current_phase = "initialization"
            last_overall_progress = 0
            last_task_progress = 0

            # Create command
            if command == "build-exe":
                cmd = [
                    python_exe,
                    "-m",
                    "installer_creator.build_exe",
                    config_file_path,
                ]
                # Add Python path argument if specified in GUI
                if hasattr(self.ui, "projectPythonPath"):
                    python_path = self.ui.projectPythonPath.text().strip()
                    if python_path:
                        cmd.extend(["--python-path", python_path])
                if args:
                    cmd.extend(args)
                signals.output.emit(f"Running command: {' '.join(cmd)}\n")

                # Get output directory from config
                try:
                    with open(config_file_path, "r") as f:
                        config = yaml.safe_load(f)

                    output_dir = (
                        config.get("build", {})
                        .get("output", {})
                        .get("directory", "dist")
                    )

                    if not os.path.isabs(output_dir):
                        output_dir = os.path.join(project_dir, output_dir)
                except Exception as e:
                    signals.output.emit(f"Error reading config file: {e}\n")
                    output_dir = os.path.join(project_dir, "dist")

                output_dir = os.path.normpath(output_dir)

                # Check if output directory exists
                if not os.path.exists(output_dir):
                    try:
                        signals.output.emit(
                            f"Creating output directory: {output_dir}\n"
                        )
                        os.makedirs(output_dir, exist_ok=True)
                        signals.output.emit(f"Created output directory: {output_dir}\n")
                    except Exception as e:
                        signals.output.emit(f"Error creating output directory: {e}\n")
                        signals.output.emit(
                            f"Debug: Exception details: {type(e).__name__}: {str(e)}\n"
                        )

                # Check if we have write permission to the output directory
                if os.path.exists(output_dir):
                    if os.access(output_dir, os.W_OK):
                        signals.output.emit(
                            f"Output directory is writable: {output_dir}\n"
                        )
                    else:
                        signals.output.emit(
                            f"Warning: Output directory is not writable: {output_dir}\n"
                        )
                        signals.output.emit(
                            "You may need to run as administrator or change the output directory.\n"
                        )

                # Run the command
                try:
                    # Use winpty for Windows terminal handling if available
                    if (
                        sys.platform == "win32"
                        and "winpty" in sys.modules
                        and winpty is not None
                    ):
                        signals.output.emit(
                            "Using winpty for Windows terminal handling...\n"
                        )

                        # Convert command list to a single string for display
                        cmd_str = " ".join(cmd)
                        signals.output.emit(f"Executing command: {cmd_str}\n")

                        # Create a winpty terminal
                        term = winpty.PtyProcess.spawn(cmd, cwd=project_dir, env=env)
                        self.active_process = term

                        # Initialize dual progress tracking
                        line_count = 0
                        
                        # Set initial progress
                        signals.overall_progress.emit(overall_phases[current_phase]["start"])
                        signals.overall_status.emit(overall_phases[current_phase]["message"])
                        signals.task_progress.emit(0)
                        signals.task_status.emit("Waiting for Nuitka output...")

                        # Read output in a loop
                        while True:
                            try:
                                # Check if process was cancelled
                                if (
                                    not hasattr(self, "active_process")
                                    or self.active_process is None
                                ):
                                    signals.output.emit(
                                        "Build process was cancelled.\n"
                                    )
                                    break

                                # Read output with timeout
                                output = term.read()
                                if output:
                                    # Process each line
                                    for line in output.splitlines(
                                        True
                                    ):  # Keep line endings
                                        # Clean the line from terminal control characters
                                        clean_line = re.sub(
                                            r"\x1b\[[0-9;]*[a-zA-Z]", "", line
                                        )

                                        # Send output to UI
                                        signals.output.emit(clean_line)
                                        line_count += 1
                                        
                                        # Debug: Log lines that might contain progress info
                                        if any(pattern in clean_line.lower() for pattern in ['%', 'compiling', 'progress', 'done', 'nuitka', 'payload']):
                                            signals.output.emit(f"[DEBUG] Progress line detected: {clean_line.strip()}\n")

                                        # Check for explicit progress markers
                                        if clean_line.strip().startswith("PROGRESS:"):
                                            try:
                                                parts = clean_line.strip().split(":", 2)
                                                if len(parts) >= 2:
                                                    progress = int(parts[1])
                                                    message = (
                                                        parts[2].strip()
                                                        if len(parts) > 2
                                                        else ""
                                                    )
                                                    # Use legacy signals for explicit progress markers
                                                    signals.progress.emit(progress)
                                                    if message:
                                                        signals.status.emit(message)
                                                continue
                                            except (ValueError, IndexError):
                                                pass

                                        # Enhanced dual progress tracking based on Nuitka output
                                        progress_updated = False
                                        
                                        # Unified progress bar pattern matching
                                        # Matches formats like:
                                        # - "PASS 1: 72.5%|████████████████████▏ | 174/240, module_name"
                                        # - "Onefile Payload: 1.1%|▎ | 102/9164, numpy.libs\msvcp140-"
                                        # - "Any Task: 45.2%|██████████ | 123/456, item_name"
                                        progress_bar_match = re.search(r'([^:]+):\s+(\d+(?:\.\d+)?)%.*?\|\s*(\d+)/(\d+),\s*(.+)', clean_line)
                                        if progress_bar_match:
                                            task_type = progress_bar_match.group(1).strip()
                                            nuitka_percent = float(progress_bar_match.group(2))
                                            current_item = int(progress_bar_match.group(3))
                                            total_items = int(progress_bar_match.group(4))
                                            item_name = progress_bar_match.group(5).strip()
                                            
                                            # Create appropriate status message based on task type
                                            if "PASS" in task_type.upper():
                                                status_msg = f"Compiling {item_name} ({current_item}/{total_items}) - {nuitka_percent:.1f}%"
                                            elif "ONEFILE" in task_type.upper() or "PAYLOAD" in task_type.upper():
                                                status_msg = f"Packaging {item_name} ({current_item}/{total_items}) - {nuitka_percent:.1f}%"
                                            else:
                                                status_msg = f"{task_type}: {item_name} ({current_item}/{total_items}) - {nuitka_percent:.1f}%"
                                            
                                            # Update task progress bar
                                            signals.task_progress.emit(int(nuitka_percent))
                                            signals.task_status.emit(status_msg)
                                            signals.output.emit(f"[DEBUG] Progress bar matched: {task_type} - {nuitka_percent:.1f}%\n")
                                            progress_updated = True
                                        
                                        # Fallback: Simple percentage patterns for other formats
                                        elif not progress_updated:
                                            # Match [XX%], "XX% done", "Progress: XX%", etc.
                                            simple_percent_match = re.search(r'(\d+(?:\.\d+)?)%', clean_line)
                                            if simple_percent_match and any(keyword in clean_line.lower() for keyword in ['progress', 'done', 'compiling', 'building', 'generating']):
                                                nuitka_percent = float(simple_percent_match.group(1))
                                                signals.task_progress.emit(int(nuitka_percent))
                                                
                                                # Clean up the status message
                                                task_desc = clean_line.strip()
                                                if len(task_desc) > 100:
                                                    task_desc = task_desc[:97] + "..."
                                                signals.task_status.emit(task_desc)
                                                signals.output.emit(f"[DEBUG] Simple percentage matched: {nuitka_percent:.1f}%\n")
                                                progress_updated = True
                                        
                                        # Check for phase markers if no percentage was found
                                        if not progress_updated:
                                            for marker, marker_info in nuitka_markers.items():
                                                if marker.lower() in clean_line.lower():
                                                    # Update current phase if needed
                                                    new_phase = marker_info["phase"]
                                                    if new_phase != current_phase:
                                                        current_phase = new_phase
                                                        # Reset task progress when entering new phase
                                                        signals.task_progress.emit(0)
                                                        last_task_progress = 0
                                                    
                                                    # Calculate overall progress based on phase
                                                    phase_info = overall_phases[current_phase]
                                                    phase_range = phase_info["end"] - phase_info["start"]
                                                    task_progress_in_phase = (marker_info["task_progress"] / 100) * phase_range
                                                    new_overall_progress = int(phase_info["start"] + task_progress_in_phase)
                                                    
                                                    # Update progress bars only if progress increased
                                                    if new_overall_progress > last_overall_progress:
                                                        signals.overall_progress.emit(new_overall_progress)
                                                        signals.overall_status.emit(phase_info["message"])
                                                        last_overall_progress = new_overall_progress
                                                    
                                                    # Update task progress
                                                    if marker_info["task_progress"] > last_task_progress:
                                                        signals.task_progress.emit(marker_info["task_progress"])
                                                        signals.task_status.emit(marker_info["message"])
                                                        last_task_progress = marker_info["task_progress"]
                                                    
                                                    break
                                        
                                        # Additional patterns for common Nuitka output
                                        # Pattern for "Compiling module 'xyz' (X of Y)"
                                        module_match = re.search(r'compiling.*?\((\d+)\s+of\s+(\d+)\)', clean_line, re.IGNORECASE)
                                        if module_match and not progress_updated:
                                            current_module = int(module_match.group(1))
                                            total_modules = int(module_match.group(2))
                                            if total_modules > 0:
                                                module_percent = int((current_module / total_modules) * 100)
                                                signals.task_progress.emit(module_percent)
                                                signals.task_status.emit(f"Compiling module {current_module} of {total_modules}")
                                                progress_updated = True

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
                            signals.output.emit(
                                f"Command failed with return code {return_code}\n"
                            )
                        else:
                            signals.output.emit("Command completed successfully\n")
                            signals.progress.emit(100)
                            signals.status.emit("Build completed successfully!")
                    else:
                        # Fallback to subprocess for non-Windows or if winpty is not available
                        signals.output.emit(
                            f"Using standard subprocess on {sys.platform}...\n"
                        )

                        # Convert command list to string for display
                        cmd_str = " ".join(
                            (
                                f'"{arg}"'
                                if " " in str(arg) or "\\" in str(arg)
                                else str(arg)
                            )
                            for arg in cmd
                        )
                        signals.output.emit(f"Executing command: {cmd_str}\n")

                        # Create process with platform-specific settings
                        if sys.platform == "win32":
                            process = subprocess.Popen(
                                cmd_str,
                                shell=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                bufsize=1,
                                universal_newlines=True,
                                cwd=project_dir,
                                env=env,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
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
                                preexec_fn=os.setsid,  # Use process group for Linux
                            )

                        # Store the active process
                        self.active_process = process

                        # Initialize dual progress tracking for subprocess
                        line_count = 0
                        
                        # Set initial progress for subprocess
                        signals.overall_progress.emit(overall_phases[current_phase]["start"])
                        signals.overall_status.emit(overall_phases[current_phase]["message"])
                        signals.task_progress.emit(0)
                        signals.task_status.emit("Waiting for Nuitka output...")

                        # Process output in real-time
                        for line in iter(process.stdout.readline, ""):
                            # Check if process was cancelled
                            if (
                                not hasattr(self, "active_process")
                                or self.active_process is None
                            ):
                                signals.output.emit("Build process was cancelled.\n")
                                break

                            # Clean the line from terminal control characters
                            clean_line = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", line)

                            # Send output to UI
                            signals.output.emit(clean_line)
                            line_count += 1
                            
                            # Debug: Log lines that might contain progress info
                            if any(pattern in clean_line.lower() for pattern in ['%', 'compiling', 'progress', 'done', 'nuitka']):
                                signals.output.emit(f"[DEBUG] Progress line detected: {clean_line.strip()}\n")

                            # Check for explicit progress markers
                            if clean_line.strip().startswith("PROGRESS:"):
                                try:
                                    parts = clean_line.strip().split(":", 2)
                                    if len(parts) >= 2:
                                        progress = int(parts[1])
                                        message = (
                                            parts[2].strip() if len(parts) > 2 else ""
                                        )
                                        # Use legacy signals for explicit progress markers
                                        signals.progress.emit(progress)
                                        if message:
                                            signals.status.emit(message)
                                    continue
                                except (ValueError, IndexError):
                                    pass

                            # Enhanced dual progress tracking based on Nuitka output (subprocess version)
                            progress_updated = False
                            
                            # First, check for Nuitka percentage progress patterns
                            # Pattern 1: PASS 1: XX.X%|████████████████████▏ | 174/240, module_name (main Nuitka progress)
                            pass_match = re.search(r'PASS\s+\d+:\s+(\d+(?:\.\d+)?)%.*?\|\s*(\d+)/(\d+),\s*(.+)', clean_line)
                            if pass_match:
                                nuitka_percent = float(pass_match.group(1))
                                current_item = int(pass_match.group(2))
                                total_items = int(pass_match.group(3))
                                module_name = pass_match.group(4).strip()
                                
                                # Update task progress bar with Nuitka's internal progress
                                signals.task_progress.emit(int(nuitka_percent))
                                signals.task_status.emit(f"Compiling {module_name} ({current_item}/{total_items}) - {nuitka_percent:.1f}%")
                                progress_updated = True
                            
                            # Pattern 2: [XX%] format
                            elif re.search(r'\[(\d+)%\]', clean_line):
                                percent_match = re.search(r'\[(\d+)%\]', clean_line)
                                if percent_match:
                                    nuitka_percent = int(percent_match.group(1))
                                    signals.task_progress.emit(nuitka_percent)
                                    
                                    task_desc = clean_line.strip()
                                    if len(task_desc) > 100:
                                        task_desc = task_desc[:97] + "..."
                                    signals.task_status.emit(task_desc)
                                    progress_updated = True
                            
                            # Pattern 3: "XX% done" format
                            elif re.search(r'(\d+)%\s+done', clean_line, re.IGNORECASE):
                                percent_match = re.search(r'(\d+)%', clean_line)
                                if percent_match:
                                    nuitka_percent = int(percent_match.group(1))
                                    signals.task_progress.emit(nuitka_percent)
                                    signals.task_status.emit(clean_line.strip())
                                    progress_updated = True
                            
                            # Pattern 4: "Progress: XX%" format
                            elif re.search(r'progress:?\s*(\d+)%', clean_line, re.IGNORECASE):
                                percent_match = re.search(r'(\d+)%', clean_line)
                                if percent_match:
                                    nuitka_percent = int(percent_match.group(1))
                                    signals.task_progress.emit(nuitka_percent)
                                    signals.task_status.emit(clean_line.strip())
                                    progress_updated = True
                            
                            # Check for phase markers if no percentage was found
                            if not progress_updated:
                                for marker, marker_info in nuitka_markers.items():
                                    if marker.lower() in clean_line.lower():
                                        # Update current phase if needed
                                        new_phase = marker_info["phase"]
                                        if new_phase != current_phase:
                                            current_phase = new_phase
                                            # Reset task progress when entering new phase
                                            signals.task_progress.emit(0)
                                            last_task_progress = 0
                                        
                                        # Calculate overall progress based on phase
                                        phase_info = overall_phases[current_phase]
                                        phase_range = phase_info["end"] - phase_info["start"]
                                        task_progress_in_phase = (marker_info["task_progress"] / 100) * phase_range
                                        new_overall_progress = int(phase_info["start"] + task_progress_in_phase)
                                        
                                        # Update progress bars only if progress increased
                                        if new_overall_progress > last_overall_progress:
                                            signals.overall_progress.emit(new_overall_progress)
                                            signals.overall_status.emit(phase_info["message"])
                                            last_overall_progress = new_overall_progress
                                        
                                        # Update task progress
                                        if marker_info["task_progress"] > last_task_progress:
                                            signals.task_progress.emit(marker_info["task_progress"])
                                            signals.task_status.emit(marker_info["message"])
                                            last_task_progress = marker_info["task_progress"]
                                        
                                        break
                            
                            # Additional patterns for common Nuitka output
                            # Pattern for "Compiling module 'xyz' (X of Y)"
                            module_match = re.search(r'compiling.*?\((\d+)\s+of\s+(\d+)\)', clean_line, re.IGNORECASE)
                            if module_match and not progress_updated:
                                current_module = int(module_match.group(1))
                                total_modules = int(module_match.group(2))
                                if total_modules > 0:
                                    module_percent = int((current_module / total_modules) * 100)
                                    signals.task_progress.emit(module_percent)
                                    signals.task_status.emit(f"Compiling module {current_module} of {total_modules}")
                                    progress_updated = True

                        # Wait for process to finish
                        return_code = process.wait()

                        # Clear the active process
                        self.active_process = None

                        # Check return code
                        if return_code != 0:
                            signals.output.emit(
                                f"Command failed with return code {return_code}\n"
                            )
                        else:
                            signals.output.emit("Command completed successfully\n")
                            signals.progress.emit(100)
                            signals.status.emit("Build completed successfully!")

                    # Signal that the command is finished
                    signals.finished.emit(0 if return_code == 0 else 1)

                except Exception as e:
                    signals.output.emit(f"Error running command: {e}\n")
                    import traceback

                    signals.output.emit(
                        f"Exception details: {traceback.format_exc()}\n"
                    )

                    # Clear the active process
                    if hasattr(self, "active_process"):
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
            if hasattr(self, "active_process"):
                self.active_process = None
            # Signal that the command is finished
            signals.finished.emit(1)
        finally:
            # Clear the active process reference
            if hasattr(self, "active_process"):
                self.active_process = None
            # Reset the building flag
            self.is_building = False

    def append_output(self, text):
        """Append command output to the GUI, handling carriage-return progress updates and collapsing excessive blank lines."""
        if not text:
            return

        # Skip internal progress markers (parsed separately for the progress bar)
        if text.strip().startswith("PROGRESS:"):
            return

        ansi_re = re.compile(
            r"\x1b\[[0-9;?]*[A-Za-z]"
        )  # also strips sequences like "\x1b[?25h"

        # Carriage-return handling – keep only content AFTER the last CR
        if "\r" in text:
            after_cr = text.split("\r")[-1]
            cleaned = ansi_re.sub("", after_cr)

            # If CR segment ends with newline, rewrite the line then start a new one
            if cleaned.endswith("\n"):
                self._replace_last_line(cleaned.rstrip("\n"))
                self._append_plain("\n")
            else:
                self._replace_last_line(cleaned)
            return

        # Normal incremental output path
        cleaned = ansi_re.sub("", text)

        # Collapse 3+ successive newlines to 2
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

        self._append_plain(cleaned)

    def _replace_last_line(self, new_text: str):
        """Replace the content of the last line in the output widget with ``new_text`` (no newline)."""
        cursor = self.ui.outputTextEdit.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.select(QTextCursor.LineUnderCursor)
        cursor.removeSelectedText()
        cursor.insertText(new_text)
        self.ui.outputTextEdit.setTextCursor(cursor)
        self.ui.outputTextEdit.ensureCursorVisible()

    def _append_plain(self, text: str):
        """Append text exactly as given to the output widget."""
        if not text:
            return
        cursor = self.ui.outputTextEdit.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.ui.outputTextEdit.setTextCursor(cursor)
        self.ui.outputTextEdit.ensureCursorVisible()

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def command_finished(self, return_code):
        """Handle command completion."""
        # Separator
        self.ui.outputTextEdit.append("=" * 50)
        # Summary line
        if return_code == 0:
            self.ui.outputTextEdit.append("Command completed successfully.")
        else:
            self.ui.outputTextEdit.append(
                f"Command failed with return code {return_code}."
            )

        # Close the progress dialog if it exists
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.accept()

        # Clear the active process reference
        self.active_process = None

        # Reset the building flag
        self.is_building = False

        # Re-enable UI
        self.set_ui_enabled(True)

        # Reset modified state after build, assuming config was saved before build
        # self.set_modified(False) # Might be too aggressive, config might still be modified if build fails early

    def update_overall_progress(self, value):
        """Update the overall progress bar."""
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.update_overall_progress(value)

    def update_overall_status(self, text):
        """Update the overall status text."""
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.update_overall_status(text)

    def update_task_progress(self, value):
        """Update the current task progress bar."""
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.update_task_progress(value)

    def update_task_status(self, text):
        """Update the current task status text."""
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.update_task_status(text)

    def update_progress(self, value):
        """Update the progress bar (legacy method)."""
        self.update_overall_progress(value)

    def update_status(self, text):
        """Update the status text (legacy method)."""
        self.update_overall_status(text)

    def build_executable(self):
        """Build the executable using the current configuration."""
        if self.is_building:
            self.append_output(
                "\n[warning]Build already in progress. Please wait or cancel.[/warning]\n"
            )
            QMessageBox.warning(
                self.ui,
                "Build in Progress",
                "A build is already running. Please wait for it to complete or cancel it.",
            )
            return

        self.is_building = True
        self.set_ui_enabled(False)  # Disable UI
        self.run_command("build-exe")

    def build_installer(self):
        """Build the installer using the current configuration."""
        if self.is_building:
            self.append_output(
                "\n[warning]Build already in progress. Please wait or cancel.[/warning]\n"
            )
            QMessageBox.warning(
                self.ui,
                "Build in Progress",
                "A build is already running. Please wait for it to complete or cancel it.",
            )
            return

        self.is_building = True
        self.set_ui_enabled(False)  # Disable UI
        self.run_command("build-installer")

    def clear_output(self):
        """Clear the output text area."""
        self.ui.outputTextEdit.clear()

        # Reset the building flag
        self.is_building = False

        # Re-enable UI
        self.set_ui_enabled(True)

        # Reset modified state after build, assuming config was saved before build
        # self.set_modified(False) # Might be too aggressive, config might still be modified if build fails early

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
                self.set_modified(True)
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
                self.set_modified(True)

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
                self.set_modified(True)
        else:
            # Browse for a directory
            dir_path = QFileDialog.getExistingDirectory(
                self.ui, "Select Directory", abs_path
            )
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
                self.set_modified(True)

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
            self.set_modified(True)

    def cancel_build(self):
        """Cancel the current build process."""
        if not self.is_building:
            return  # Nothing to cancel

        print("Canceling build...")
        if hasattr(self, "active_process") and self.active_process is not None:
            print("Cancelling active process...")
            try:
                # Check if the active process is a winpty process
                if (
                    hasattr(self.active_process, "terminate")
                    and callable(getattr(self.active_process, "terminate"))
                    and "winpty" in sys.modules
                    and winpty is not None
                ):
                    # For winpty processes
                    self.append_output("Terminating winpty process...\n")
                    self.active_process.terminate(force=True)
                elif hasattr(self.active_process, "kill") and callable(
                    getattr(self.active_process, "kill")
                ):
                    # For other process types with kill method
                    self.append_output("Killing process...\n")
                    self.active_process.kill()
                else:
                    # For subprocess processes
                    self.append_output("Terminating subprocess...\n")
                    if sys.platform == "win32":
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

                            os.killpg(
                                os.getpgid(self.active_process.pid), signal.SIGTERM
                            )
                            self.append_output("Sent SIGTERM to process group\n")

                            # Give it a moment to terminate gracefully
                            import time

                            time.sleep(0.5)

                            # If still running, force kill
                            if self.active_process.poll() is None:
                                os.killpg(
                                    os.getpgid(self.active_process.pid), signal.SIGKILL
                                )
                                self.append_output("Sent SIGKILL to process group\n")
                        except Exception as e:
                            # Fallback to terminate if process group handling fails
                            self.append_output(f"Error killing process group: {e}\n")
                            self.active_process.terminate()

                # Clear the active process reference
                self.active_process = None

                # Update the UI
                if hasattr(self, "progress_dialog") and self.progress_dialog:
                    self.progress_dialog.update_status("Build cancelled")
                    self.progress_dialog.close()

                # Append to output
                self.append_output("Build process was cancelled by user.\n")

            except Exception as e:
                self.append_output(f"Error cancelling build: {e}\n")
                # Clear the active process reference
                self.active_process = None

        # Reset the building flag
        self.is_building = False

        # Re-enable UI
        self.set_ui_enabled(True)

    def closeEvent(self, event):
        """Handle the window close event, prompting to save if modified."""
        if self.config_modified:
            reply = QMessageBox.question(
                self.ui,
                "Save Changes",
                "There are unsaved changes. Do you want to save before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )

            if reply == QMessageBox.Save:
                self.save_config()
                # Check if save was successful (e.g., by checking modified flag again, though save_config resets it)
                # For simplicity, assume save worked if no exception occurred
                event.accept()
            elif reply == QMessageBox.Discard:
                event.accept()
            else:  # Cancel
                event.ignore()
        else:
            event.accept()


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
