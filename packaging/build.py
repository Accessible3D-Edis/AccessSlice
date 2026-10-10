"""build.py - build the packaged AccessSlice program and its installer.

    python packaging\\build.py            bundle + installer
    python packaging\\build.py --bundle   the PyInstaller bundle only

Steps, in order:

1. Write packaging\\build\\version_info.txt from app\\version.py so the
   .exe carries the version in its file properties.
2. PyInstaller one-folder bundle from packaging\\AccessSlice.spec into
   packaging\\dist\\AccessSlice\\ (AccessSlice.exe + _internal).
3. Inno Setup (ISCC.exe) compiles packaging\\AccessSlice.iss into
   packaging\\output\\AccessSlice-Setup-<version>.exe.

Code signing happens in the same run when packaging\\signing.json
exists (it is git-ignored; packaging\\signing.example.json shows the
shape and packaging\\README.md the options): AccessSlice.exe is
signed after step 2, and Inno Setup signs the installer and the
uninstaller it embeds. Without that file the build is unsigned, which
is how the first release ships. --unsigned skips signing even when
the file exists.

Needs, on the build machine only (users need none of this): Python
with PyInstaller and pywebview installed, and Inno Setup 6. Everything
under packaging\\build, dist and output is ignored by git.

After a build, run the packaged-program tests, which start the
built .exe and drive it over HTTP:

    python -m unittest tests.test_packaged -v
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
sys.path.insert(0, str(ROOT / "app"))
from version import VERSION  # noqa: E402

ISCC_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6"
    / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


SIGNING_CONFIG = PACKAGING / "signing.json"
DEFAULT_TIMESTAMP = "http://timestamp.digicert.com"


def find_signtool():
    """signtool.exe from the newest installed Windows SDK, or None."""
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) \
        / "Windows Kits" / "10" / "bin"
    found = sorted(kits.glob("*/x64/signtool.exe")) if kits.is_dir() else []
    return found[-1] if found else None


def signing_config(path=SIGNING_CONFIG):
    """The local signing configuration, or None for an unsigned build.

    Keys: "sign_args" (list, required) - the signtool arguments that
    pick the certificate, e.g. ["/sha1", "<thumbprint>"] for one in the
    user's certificate store, or ["/dlib", ..., "/dmdf", ...] for Azure
    Trusted Signing; "signtool" (optional path; found in the Windows
    SDK otherwise); "timestamp" (optional RFC 3161 URL; DigiCert's by
    default). Never put a .pfx password in this file.
    """
    if not Path(path).is_file():
        return None
    # utf-8-sig: PowerShell's Set-Content writes a byte-order mark.
    cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    args = cfg.get("sign_args")
    if not isinstance(args, list) or not args:
        raise SystemExit(f"{path}: \"sign_args\" must be a non-empty list")
    tool = Path(cfg["signtool"]) if cfg.get("signtool") else find_signtool()
    if tool is None or not Path(tool).is_file():
        raise SystemExit("signtool.exe was not found - install the Windows "
                         "SDK (Windows App Certification Kit is enough) "
                         "or set \"signtool\" in signing.json")
    return {"signtool": Path(tool), "sign_args": [str(a) for a in args],
            "timestamp": cfg.get("timestamp") or DEFAULT_TIMESTAMP}


def sign_command(cfg, path):
    """The signtool command that signs one file: SHA-256 digest, an
    RFC 3161 timestamp (the signature outlives the certificate), the
    configured certificate selection, the file."""
    return ([str(cfg["signtool"]), "sign", "/fd", "SHA256",
             "/tr", cfg["timestamp"], "/td", "SHA256",
             "/d", "AccessSlice"] + cfg["sign_args"] + [str(path)])


def inno_sign_command(cfg):
    """The same command as an Inno Setup SignTool definition: $f is the
    file Inno substitutes, $q a double quote."""
    words = sign_command(cfg, "$f")[:-1]
    quoted = " ".join(f"$q{w}$q" if " " in w else w for w in words)
    return f"{quoted} $f"


def sign_file(cfg, path):
    run(sign_command(cfg, path))
    run([str(cfg["signtool"]), "verify", "/pa", "/v", str(path)],
        check=False)
    print(f"signed: {path}")


def version_tuple():
    parts = [int(p) for p in VERSION.split(".")][:4]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


def write_version_info():
    """The Windows file-version resource PyInstaller stamps on the exe."""
    nums = ", ".join(str(n) for n in version_tuple())
    text = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({nums}),
    prodvers=({nums}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'AccessSlice'),
        StringStruct('FileDescription', 'AccessSlice - accessible slicing for 3D printing'),
        StringStruct('FileVersion', '{VERSION}'),
        StringStruct('InternalName', 'AccessSlice'),
        StringStruct('OriginalFilename', 'AccessSlice.exe'),
        StringStruct('ProductName', 'AccessSlice'),
        StringStruct('ProductVersion', '{VERSION}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    target = PACKAGING / "build" / "version_info.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def run(cmd, check=True, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=check, cwd=str(ROOT), **kw)


def build_bundle(signing=None):
    write_version_info()
    dist = PACKAGING / "dist" / "AccessSlice"
    if dist.exists():
        shutil.rmtree(dist)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", PACKAGING / "dist", "--workpath", PACKAGING / "build",
         PACKAGING / "AccessSlice.spec"])
    exe = dist / "AccessSlice.exe"
    if not exe.is_file():
        raise SystemExit(f"PyInstaller finished but {exe} is missing")
    print(f"bundle: {exe}")
    if signing:
        sign_file(signing, exe)
    return exe


def find_iscc():
    for candidate in ISCC_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit("Inno Setup 6 (ISCC.exe) was not found - install it "
                     "from jrsoftware.org or with winget "
                     "(JRSoftware.InnoSetup), then build again.")


def build_installer(signing=None):
    out = PACKAGING / "output"
    out.mkdir(exist_ok=True)
    cmd = [find_iscc(), f"/DAppVersion={VERSION}", f"/O{out}"]
    if signing:
        # /S defines the sign tool on the command line; the script's
        # SignTool=accessslice line (under #ifdef Sign) refers to it.
        cmd += ["/DSign=1", f"/Saccessslice={inno_sign_command(signing)}"]
    run(cmd + [PACKAGING / "AccessSlice.iss"])
    setup = out / f"AccessSlice-Setup-{VERSION}.exe"
    if not setup.is_file():
        raise SystemExit(f"Inno Setup finished but {setup} is missing")
    print(f"installer: {setup}" + (" (signed)" if signing else " (unsigned)"))
    return setup


def main(argv):
    signing = None if "--unsigned" in argv else signing_config()
    print("signing:", "configured" if signing else "none (unsigned build)")
    build_bundle(signing)
    if "--bundle" not in argv:
        build_installer(signing)


if __name__ == "__main__":
    main(sys.argv[1:])
