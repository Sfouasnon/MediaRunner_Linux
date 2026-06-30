# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['mediarunner_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets'), ('validation', 'validation'), ('MediaRunner_LOGO.png', '.'), ('MediaRunner_LOGO_HTML.png', '.'), ('MediaRunner_REPORT_LOGO.png', '.'), ('mediarunner_core.py', '.'), ('mediarunner_ftp.py', '.'), ('mediarunner_transfer.py', '.'), ('mediarunner_meta.py', '.'), ('mediarunner_reports.py', '.'), ('mediarunner_red_wireless.py', '.'), ('mediarunner_mhl.py', '.'), ('mediarunner_logging.py', '.'), ('mediarunner_notifications.py', '.'), ('mediarunner_linux_ingest.py', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['scipy', 'matplotlib', 'pyarrow', 'PIL', 'jinja2', 'IPython', 'notebook'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MediaRunner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MediaRunner',
)
app = BUNDLE(
    coll,
    name='MediaRunner.app',
    icon=None,
    bundle_identifier=None,
)
