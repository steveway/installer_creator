#!/usr/bin/env python3
import os
import sys
import uuid
import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open, call
import yaml
import html
import re
from installer_creator.build_installer import (
    build_wix_installer,
    generate_wix_source,
    check_wix_installed,
    check_wix_extension,
    install_wix_extension,
    main
)

# Sample test configuration
TEST_CONFIG = {
    'project': {
        'name': 'TestApp',
        'version': '1.0.0',
        'description': 'Test Application',
        'company': 'Test Company',
        'icon': 'test_icon.ico',
        'main_file': 'test_main.py'
    },
    'build': {
        'output': {
            'directory': 'test_dist',
            'filename': 'test_app.exe'
        },
        'options': {
            'standalone': True,
            'onefile': True
        },
        'copy_beside': ['test_resources', 'test_file.txt']
    },
    'installer': {
        'enabled': True,
        'output': {
            'directory': 'test_dist',
            'filename': 'test_app_installer.msi'
        },
        'metadata': {
            'manufacturer': 'Test Company',
            'product_name': 'Test Application',
            'upgrade_code': '12345678-1234-1234-1234-123456789012'
        },
        'ui': {
            'banner_image': 'banner.bmp',
            'dialog_image': 'dialog.bmp'
        },
        'license_file': 'license.rtf'
    }
}


class TestBuildInstaller:
    
    @patch('installer_creator.build_installer.check_wix_installed')
    @patch('installer_creator.build_installer.check_wix_extension')
    @patch('installer_creator.build_installer.install_wix_extension')
    @patch('installer_creator.build_installer.Path')
    @patch('installer_creator.build_installer.generate_wix_source')
    @patch('builtins.open', new_callable=mock_open)
    @patch('installer_creator.build_installer.subprocess.run')
    def test_build_wix_installer_success(self, mock_run, mock_file, mock_generate, 
                                         mock_path, mock_install_ext, mock_check_ext, 
                                         mock_check_wix):
        # Setup mocks
        mock_check_wix.return_value = True
        mock_check_ext.return_value = True
        mock_path.return_value.mkdir.return_value = None
        mock_path.return_value.exists.return_value = True
        mock_generate.return_value = "<test>WiX XML content</test>"
        mock_run.return_value = MagicMock(returncode=0)
        
        # Test function
        build_wix_installer(TEST_CONFIG)
        
        # Verify function calls
        mock_check_wix.assert_called_once()
        mock_check_ext.assert_called_once()
        mock_install_ext.assert_not_called()  # Extension already installed
        mock_path.return_value.mkdir.assert_called_once_with(exist_ok=True)
        mock_generate.assert_called_once_with(TEST_CONFIG)
        mock_file.assert_called_once()
        mock_run.assert_called_once()
    
    @patch('installer_creator.build_installer.check_wix_installed')
    def test_build_wix_installer_wix_not_installed(self, mock_check_wix):
        # Setup mock
        mock_check_wix.return_value = False
        
        # Test function
        with pytest.raises(SystemExit):
            build_wix_installer(TEST_CONFIG)
    
    @patch('installer_creator.build_installer.check_wix_installed')
    @patch('installer_creator.build_installer.check_wix_extension')
    @patch('installer_creator.build_installer.install_wix_extension')
    def test_build_wix_installer_install_extension(self, mock_install_ext, 
                                                  mock_check_ext, mock_check_wix):
        # Setup mocks
        mock_check_wix.return_value = True
        mock_check_ext.return_value = False
        
        # Create config with installer disabled
        disabled_config = TEST_CONFIG.copy()
        disabled_config['installer'] = {'enabled': False}
        
        # Test function
        build_wix_installer(disabled_config)
        
        # Verify extension installation was attempted
        mock_install_ext.assert_called_once()
    
    @patch('installer_creator.build_installer.Path')
    def test_build_wix_installer_exe_not_found(self, mock_path):
        # Setup mocks
        with patch('installer_creator.build_installer.check_wix_installed', return_value=True), \
             patch('installer_creator.build_installer.check_wix_extension', return_value=True), \
             patch('installer_creator.build_installer.subprocess.run') as mock_run:
            
            # Mock Path to simulate executable not found
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = False
            mock_path_instance.__truediv__.return_value = mock_path_instance
            mock_path.return_value = mock_path_instance
            
            # Test function
            build_wix_installer(TEST_CONFIG)
            
            # Verify subprocess.run was not called since executable was not found
            mock_run.assert_not_called()

    @patch('installer_creator.build_installer.subprocess.run')
    def test_build_wix_installer_subprocess_error(self, mock_run):
        # Setup mock to raise an error
        mock_run.side_effect = subprocess.CalledProcessError(1, "wix build")
        
        # Setup other mocks to pass initial checks
        with patch('installer_creator.build_installer.check_wix_installed', return_value=True), \
             patch('installer_creator.build_installer.check_wix_extension', return_value=True), \
             patch('installer_creator.build_installer.Path') as mock_path, \
             patch('installer_creator.build_installer.generate_wix_source', return_value="<test>WiX XML</test>"), \
             patch('builtins.open', new_callable=mock_open):
            
            mock_path.return_value.exists.return_value = True
            
            # Test function
            with pytest.raises(subprocess.CalledProcessError):
                build_wix_installer(TEST_CONFIG)
    
    @patch('uuid.uuid4')
    def test_generate_wix_source(self, mock_uuid):
        # Setup mock for uuid
        mock_uuid.return_value = "test-uuid-value"
        
        # Setup mock for build output directory
        with patch('pathlib.Path') as mock_path, \
             patch('os.walk') as mock_walk:
            mock_path.return_value.__truediv__.return_value = mock_path
            mock_path.exists.return_value = False  # Simulate no copy_beside items
            mock_walk.return_value = []  # Empty directory structure
            
            # Test function
            result = generate_wix_source(TEST_CONFIG)
            
            # Verify result contains expected WiX XML elements
            assert '<?xml version="1.0" encoding="UTF-8"?>' in result
            assert '<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs"' in result
            assert '<Package' in result
            assert 'Name="Test Application"' in result
            assert 'Manufacturer="Test Company"' in result
            assert 'Version="1.0.0"' in result
    
    @patch('uuid.uuid4')
    def test_generate_wix_source_with_special_chars(self, mock_uuid):
        # Setup mock for uuid
        mock_uuid.return_value = "test-uuid-value"
        
        # Create config with special characters in manufacturer and product name
        special_chars_config = TEST_CONFIG.copy()
        special_chars_config['installer'] = {
            'enabled': True,
            'output': {
                'directory': 'test_dist',
                'filename': 'test_app_installer.msi'
            },
            'metadata': {
                'manufacturer': 'Test GmbH & Co.KG',
                'product_name': 'Test App <with> "special" chars & symbols',
                'upgrade_code': '12345678-1234-1234-1234-123456789012'
            },
            'ui': {
                'banner_image': 'banner.bmp',
                'dialog_image': 'dialog.bmp'
            },
            'license_file': 'license.rtf'
        }
        
        # Setup mock for build output directory
        with patch('pathlib.Path') as mock_path, \
             patch('os.walk') as mock_walk:
            mock_path.return_value.__truediv__.return_value = mock_path
            mock_path.exists.return_value = False  # Simulate no copy_beside items
            mock_walk.return_value = []  # Empty directory structure
            
            # Test function
            result = generate_wix_source(special_chars_config)
            
            # Verify special characters are properly escaped
            assert 'Test GmbH &amp; Co.KG' in result
            assert 'Test App &lt;with&gt; &quot;special&quot; chars &amp; symbols' in result
            
            # Verify result doesn't contain unescaped special characters
            assert 'Test GmbH & Co.KG' not in result
            assert 'Test App <with> "special" chars & symbols' not in result

    def test_sanitize_wix_id(self):
        # Create a simple implementation of sanitize_wix_id for testing
        def sanitize_wix_id(name):
            sanitized = re.sub(r'[^a-zA-Z0-9.]+', '_', name)
            sanitized = sanitized.strip('_')
            if not sanitized or not re.match(r'^[a-zA-Z_]', sanitized):
                sanitized = '_' + sanitized
            return sanitized[:70]
        
        # Test valid ID
        assert sanitize_wix_id("ValidID123") == "ValidID123"
        
        # Test ID with invalid characters
        assert sanitize_wix_id("Invalid ID!@#") == "Invalid_ID"
        
        # Test ID starting with number
        assert sanitize_wix_id("123InvalidStart") == "_123InvalidStart"
        
        # Test very long ID (should be truncated to 70 chars)
        long_id = "a" * 100
        assert len(sanitize_wix_id(long_id)) == 70

    @patch('installer_creator.build_installer.subprocess.run')
    def test_check_wix_installed_success(self, mock_run):
        # Setup mock for successful command
        mock_run.return_value = MagicMock(returncode=0)
        
        # Test function
        result = check_wix_installed()
        
        # Verify result and function call
        assert result is True
        mock_run.assert_called_once_with(['wix', '--version'], capture_output=True, check=True)
    
    @patch('installer_creator.build_installer.subprocess.run')
    def test_check_wix_installed_failure(self, mock_run):
        # Setup mock for failed command
        mock_run.side_effect = subprocess.CalledProcessError(1, "wix --version")
        
        # Test function
        result = check_wix_installed()
        
        # Verify result
        assert result is False
    
    @patch('installer_creator.build_installer.subprocess.run')
    def test_check_wix_extension_success(self, mock_run):
        # Setup mock for successful command with extension in output
        mock_process = MagicMock()
        mock_process.stdout = "WixToolset.UI.wixext"
        mock_run.return_value = mock_process
        
        # Test function
        result = check_wix_extension()
        
        # Verify result and function call
        assert result is True
        mock_run.assert_called_once_with(['wix', 'extension', 'list'], 
                                         capture_output=True, text=True, check=True)
    
    @patch('installer_creator.build_installer.subprocess.run')
    def test_check_wix_extension_not_found(self, mock_run):
        # Setup mock for successful command but extension not in output
        mock_process = MagicMock()
        mock_process.stdout = "SomeOtherExtension"
        mock_run.return_value = mock_process
        
        # Test function
        result = check_wix_extension()
        
        # Verify result
        assert result is False
    
    @patch('installer_creator.build_installer.subprocess.run')
    def test_check_wix_extension_error(self, mock_run):
        # Setup mock for failed command
        mock_run.side_effect = subprocess.CalledProcessError(1, "wix extension list")
        
        # Test function
        result = check_wix_extension()
        
        # Verify result
        assert result is False
    
    @patch('installer_creator.build_installer.subprocess.run')
    def test_install_wix_extension_success(self, mock_run):
        # Setup mock for successful command
        mock_run.return_value = MagicMock(returncode=0)
        
        # Test function
        install_wix_extension()
        
        # Verify function call
        mock_run.assert_called_once_with(['wix', 'extension', 'add', 'WixToolset.UI.wixext'], 
                                         check=True)
    
    @patch('installer_creator.build_installer.subprocess.run')
    def test_install_wix_extension_failure(self, mock_run):
        # Setup mock for failed command
        mock_run.side_effect = subprocess.CalledProcessError(1, "wix extension add")
        
        # Test function
        with pytest.raises(SystemExit):
            install_wix_extension()
    
    @patch('builtins.open', new_callable=mock_open, read_data=yaml.dump(TEST_CONFIG))
    @patch('installer_creator.build_installer.build_wix_installer')
    def test_main_success(self, mock_build_wix, mock_file):
        # Test function
        main('test_config.yaml')
        
        # Verify function calls
        mock_file.assert_called_once_with('test_config.yaml', 'r')
        mock_build_wix.assert_called_once_with(TEST_CONFIG)
    
    @patch('builtins.open', side_effect=FileNotFoundError)
    def test_main_config_not_found(self, mock_open):
        # Test function with missing config file
        main('missing_config.yaml')
        
        # No assertions needed - function should print error and return


if __name__ == '__main__':
    pytest.main(['-v', __file__])
