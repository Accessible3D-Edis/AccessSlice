# Packaging AccessSlice

Everything here turns the source tree into the program users install.
Build machine only; users need none of it.

    pip install pyinstaller pywebview
    python packaging\build.py            bundle + installer
    python packaging\build.py --bundle   the PyInstaller bundle only
    python packaging\build.py --unsigned ignore signing.json this time

- `AccessSlice.spec` - the PyInstaller recipe (one-folder, no console,
  the static folder and the Prusa defaults as data, the 3MF engine and
  every backend as bundled modules, the version resource).
- `AccessSlice.iss` - the Inno Setup script (per-user install, WebView2
  check, shortcuts, uninstaller, optional signing).
- `build.py` - runs both, in order, and signs when configured.
- `make_icon.py` - draws `accessslice.ico` from the CSS logo mark.
- `signing.example.json` - the shape of the local signing
  configuration.

Outputs (git-ignored): `build\` (PyInstaller's work folder and the
version resource), `dist\AccessSlice\` (the bundle), `output\`
(`AccessSlice-Setup-<version>.exe`). The version number is
`app\version.py`.

After a build, the suite drives the built program itself:

    python -m unittest tests.test_packaged -v

## Code signing

Signing attaches a certificate to `AccessSlice.exe`, the installer and
the uninstaller, so Windows can show who built them and that they were
not altered. It needs signtool from the Windows SDK (found
automatically) and a certificate. The build signs when
`packaging\signing.json` exists:

    {"sign_args": ["/sha1", "<thumbprint>"],
     "timestamp": "http://timestamp.digicert.com"}

`sign_args` is whatever selects your certificate for signtool;
`timestamp` is an RFC 3161 server (the signature stays valid after the
certificate expires). The file is git-ignored - never commit it, and
never put a .pfx password in it. Every file is signed with a SHA-256
digest and timestamped; `signtool verify /pa /v` is printed after each.

### Where a certificate comes from

- **SignPath Foundation** (free, open-source projects): they sign the
  release artefacts on their service after the repository is public.
  No local `signing.json`; follow their onboarding instead.
- **Azure Trusted Signing** (Microsoft, monthly fee, no hardware):
  install their signtool library, then
  `"sign_args": ["/dlib", "<Azure.CodeSigning.Dlib.dll>", "/dmdf", "<metadata.json>"]`
  with the Azure login done in the same shell.
- **A certificate on a USB token, smart card, or imported into the user
  store** (Certum, DigiCert, Sectigo, GlobalSign, SSL.com, ...):
  `"sign_args": ["/sha1", "<thumbprint>"]`. The token's own PIN dialog
  appears at each signing.

### Trying the mechanics without a real certificate

A self-signed certificate proves the pipeline end to end - Windows
still says "Unknown publisher", because nobody vouches for it:

    $c = New-SelfSignedCertificate -Type CodeSigningCert `
         -Subject "CN=AccessSlice test signing" `
         -CertStoreLocation Cert:\CurrentUser\My
    $c.Thumbprint

Put that thumbprint in `signing.json`, build, then check with
`Get-AuthenticodeSignature` on the outputs. Remove the certificate
from `Cert:\CurrentUser\My` and delete `signing.json` afterwards.
