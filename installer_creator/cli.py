import argparse
from . import build_exe
from . import build_installer
from . import changelog_generator

def main():
    parser = argparse.ArgumentParser(description='Python Installer Creator Tool')
    subparsers = parser.add_subparsers(dest='command', required=True)
    
    # Build EXE command
    build_exe_parser = subparsers.add_parser('build-exe', help='Build executable with Nuitka')
    build_exe_parser.add_argument('-c', '--config', default='build_config.yaml', help='Configuration file')
    
    # Build Installer command
    build_installer_parser = subparsers.add_parser('build-installer', help='Create Windows installer')
    build_installer_parser.add_argument('-c', '--config', default='build_config.yaml', help='Configuration file')
    
    # Generate Changelog command
    subparsers.add_parser('generate-changelog', help='Generate changelog')
    
    # Generate UUID command
    uuid_parser = subparsers.add_parser('generate-uuid', help='Generate UUID for upgrade codes')
    uuid_parser.add_argument('-s', '--string', help='String to generate deterministic UUID from')
    uuid_parser.add_argument('-r', '--random', action='store_true', help='Generate random UUID')
    
    args = parser.parse_args()
    
    if args.command == 'build-exe':
        build_exe.main(config_file=args.config)
    elif args.command == 'build-installer':
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

if __name__ == '__main__':
    main()
