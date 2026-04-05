"""
py2app setup for ShellFrame.
Usage: python setup.py py2app
"""
from setuptools import setup

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
        'CFBundleVersion': '0.2.5',
        'CFBundleShortVersionString': '0.2.5',
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
