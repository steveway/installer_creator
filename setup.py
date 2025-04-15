from setuptools import setup, find_packages

setup(
    name='installer_creator',
    version='0.1.0',
    packages=find_packages(),
    install_requires=[
        'nuitka',
        'pyyaml'
    ],
    include_package_data=True,
    entry_points={
        'console_scripts': [
            'installer-creator=installer_creator.cli:main',
        ],
    },
)
