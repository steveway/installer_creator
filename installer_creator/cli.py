import argparse
import sys
import traceback
from . import build_exe
from . import build_installer
from . import changelog_generator
from . import config_editor

def main():
    parser = argparse.ArgumentParser(description='Python Installer Creator Tool')
    subparsers = parser.add_subparsers(dest='command', required=True)
    
    # Build EXE command
    build_exe_parser = subparsers.add_parser('build-exe', help='Build executable with Nuitka')
    build_exe_parser.add_argument('-c', '--config', default='build_config.yaml', help='Configuration file')
    build_exe_parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')
    build_exe_parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
    # Build Installer command
    build_installer_parser = subparsers.add_parser('build-installer', help='Create Windows installer')
    build_installer_parser.add_argument('-c', '--config', default='build_config.yaml', help='Configuration file')
    build_installer_parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')
    build_installer_parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
    # Generate Changelog command
    subparsers.add_parser('generate-changelog', help='Generate changelog')
    
    # Generate UUID command
    uuid_parser = subparsers.add_parser('generate-uuid', help='Generate UUID for upgrade codes')
    uuid_parser.add_argument('-s', '--string', help='String to generate deterministic UUID from')
    uuid_parser.add_argument('-r', '--random', action='store_true', help='Generate random UUID')
    
    # Config Editor GUI command
    config_editor_parser = subparsers.add_parser('config-editor', help='Launch configuration editor GUI')
    config_editor_parser.add_argument('-c', '--config', default='build_config.yaml', help='Configuration file to edit')
    
    args = parser.parse_args()
    
    try:
        if args.command == 'build-exe':
            # Set environment variables for debug/verbose mode
            if hasattr(args, 'verbose') and args.verbose:
                import os
                os.environ['INSTALLER_CREATOR_VERBOSE'] = '1'
            if hasattr(args, 'debug') and args.debug:
                import os
                os.environ['INSTALLER_CREATOR_DEBUG'] = '1'
                
            build_exe.main(config_file=args.config)
        elif args.command == 'build-installer':
            # Set environment variables for debug/verbose mode
            if hasattr(args, 'verbose') and args.verbose:
                import os
                os.environ['INSTALLER_CREATOR_VERBOSE'] = '1'
            if hasattr(args, 'debug') and args.debug:
                import os
                os.environ['INSTALLER_CREATOR_DEBUG'] = '1'
                
            build_installer.main(config_file=args.config)
        elif args.command == 'generate-changelog':
            changelog_generator.main()
        elif args.command == 'generate-uuid':
            import uuid
            if args.string:
                # Generate deterministic UUID from string (version 5)
                namespace = uuid.NAMESPACE_DNS
                print(uuid.uuid5(namespace, args.string))
            elif args.random:
                # Generate random UUID (version 4)
                print(uuid.uuid4())
            else:
                print('Please specify either --string or --random')
        elif args.command == 'config-editor':
            config_editor.run(config_file=args.config)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {type(e).__name__}: {str(e)}")
        
        # Check for common errors and provide helpful messages
        if "WinError 5" in str(e):
            print("\nPermission error detected. Try the following solutions:")
            print("1. Run this application as administrator")
            print("2. Change the output directory to a location where you have write permissions")
            print("3. Close any applications that might be using the output file")
            print("4. Temporarily disable your antivirus software")
            print("5. Make sure you have write permissions to the Python installation directory")
        elif "No such file or directory" in str(e):
            print("\nFile not found error. Please check that all paths in your configuration file exist.")
        
        # Print detailed traceback in verbose mode
        import os
        if os.environ.get('INSTALLER_CREATOR_DEBUG') == '1':
            print("\nDetailed error information:")
            traceback.print_exc()
        else:
            print("\nRun with --debug flag to see detailed error information.")
        
        sys.exit(1)

if __name__ == '__main__':
    main()
