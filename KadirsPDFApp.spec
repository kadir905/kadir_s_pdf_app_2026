# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['pdf_studio.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('kadir_pdf_logo.png', '.'),
        ('kadir_pdf_logo.ico', '.'),
        ('vosk-model-small-tr-0.3', 'vosk-model-small-tr-0.3'),
    ],
    hiddenimports=[
        'googleapiclient.discovery', 'googleapiclient.http',
        'google_auth_oauthlib.flow', 'google.auth.transport.requests',
        'google.oauth2.credentials', 'httplib2', 'oauthlib.oauth2',
        'firebase_admin', 'firebase_admin.credentials', 'firebase_admin.firestore',
        'google.cloud.firestore', 'google.cloud.firestore_v1',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AstroPDFStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['kadir_pdf_logo.ico'],
)
