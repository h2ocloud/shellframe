"""
py2app setup for ShellFrame.
Usage: python setup.py py2app

Note: the canonical install path is install.sh (copies ShellFrame.app + patches
plist from version.json). This file is kept for developers who want a
from-scratch py2app build; version is read dynamically from version.json.
"""
import json
import pathlib
from setuptools import setup

_VERSION = json.loads(
    pathlib.Path(__file__).with_name('version.json').read_text()
)['version']

APP = ['main.py']
DATA_FILES = [
    ('web', ['web/index.html']),
    ('.', ['version.json', 'CHANGELOG.md']),
]
OPTIONS = {
    'argv_emulation': False,
    'iconfile': 'ShellFrame.app/Contents/Resources/shellframe.icns',
    'plist': {
        'CFBundleName': 'ShellFrame',
        'CFBundleDisplayName': 'ShellFrame',
        'CFBundleIdentifier': 'com.h2ocloud.shellframe',
        'CFBundleVersion': _VERSION,
        'CFBundleShortVersionString': _VERSION,
        'LSMinimumSystemVersion': '12.0',
        'NSHighResolutionCapable': True,
        'LSUIElement': False,
    },
    'packages': ['webview', 'objc', 'WebKit', 'Foundation', 'AppKit'],
    'includes': ['bridge_base', 'bridge_telegram'],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
