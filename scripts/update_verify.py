"""update_verify.py — validate and safely extract a Valorant Scout release ZIP.

The PowerShell updater downloads three release assets into a staging folder
and then delegates every security-relevant decision to this script:

    python update_verify.py --zip <asset.zip> --sums <SHA256SUMS.txt>
        --manifest <release-manifest.json> --dest <staging-extract-dir>
        [--expect-version 1.1.2-rc.1]

Exit 0 only if ALL of these hold:
  * the ZIP's SHA-256 matches its entry (by exact asset name) in SHA256SUMS.txt
  * every archive entry is a plain file under one root folder
    "valorant-scout-v<version>/" — no absolute/drive/UNC paths, no "..",
    no symlinks/reparse entries, no duplicate normalized names
  * the manifest describes the same version/commit/runtime schema we expect
  * after extraction, the file set EXACTLY equals the manifest's file list and
    every file's SHA-256 matches

On success the validated tree is left at <dest>/<root-folder>. Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path, PureWindowsPath

class VerifyError(Exception):
    pass

# The real artifact is ~1 MB; cap the total decompressed size so a tampered
# archive can't disk-fill the machine during extraction.
MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024  # 500 MiB

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().lower()

def parse_sums(sums_path: Path) -> dict[str, str]:
    """Parse `<hex>  <name>` lines (sha256sum format)."""
    out: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
        if m:
            out[m.group(2).strip()] = m.group(1).lower()
    return out

def check_asset_hash(asset_path: Path, sums_path: Path) -> None:
    sums = parse_sums(sums_path)
    expected = sums.get(asset_path.name)
    if not expected:
        raise VerifyError(f"SHA256SUMS.txt has no entry for '{asset_path.name}' "
                          f"(entries: {', '.join(sums) or 'none'})")
    actual = sha256_file(asset_path)
    if actual != expected:
        raise VerifyError(f"checksum mismatch for {asset_path.name}: "
                          f"expected {expected}, got {actual}")

def load_manifest(manifest_path: Path) -> dict:
    try:
        mf = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        raise VerifyError(f"release-manifest.json is unreadable: {e}") from e
    for key in ("schemaVersion", "version", "commit", "dirty", "rootFolder",
                "python", "protocol", "files"):
        if key not in mf:
            raise VerifyError(f"release-manifest.json is missing '{key}'")
    if not isinstance(mf["files"], dict) or not mf["files"]:
        raise VerifyError("release-manifest.json 'files' must be a non-empty object")
    if not re.fullmatch(r"[0-9a-f]{40}", str(mf["commit"])):
        raise VerifyError("release-manifest.json 'commit' must be a full 40-char SHA")
    if mf["schemaVersion"] != 1:
        raise VerifyError(f"unsupported release manifest schema {mf['schemaVersion']!r}")
    if not isinstance(mf["dirty"], bool):
        raise VerifyError("release-manifest.json 'dirty' must be a boolean")
    if not isinstance(mf["python"], dict) or not mf["python"].get("version") \
            or not mf["python"].get("arch"):
        raise VerifyError("release-manifest.json 'python' must contain version and arch")
    if not isinstance(mf["protocol"], int) or isinstance(mf["protocol"], bool):
        raise VerifyError("release-manifest.json 'protocol' must be an integer")
    return mf

_ENTRY_OK = re.compile(r"^[^<>:\"|?*\x00-\x1f]+$")

def validate_entry_name(name: str, root: str) -> str:
    """Return the safe relative path (under root) or raise."""
    if name.endswith("/"):
        return ""  # plain directory entry — harmless, we create dirs ourselves
    win = PureWindowsPath(name.replace("/", "\\"))
    if win.is_absolute() or win.drive or name.startswith(("/", "\\")):
        raise VerifyError(f"archive entry has an absolute/drive/UNC path: {name!r}")
    parts = name.replace("\\", "/").split("/")
    if any(p in ("", "..", ".") for p in parts):
        raise VerifyError(f"archive entry contains path traversal: {name!r}")
    if not _ENTRY_OK.match(name):
        raise VerifyError(f"archive entry contains illegal characters: {name!r}")
    if parts[0] != root:
        raise VerifyError(f"archive entry outside the release root folder: {name!r}")
    return "/".join(parts[1:])

def is_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000

def verify_and_extract(zip_path: Path, manifest: dict, dest: Path,
                       manifest_path: Path | None = None) -> Path:
    root = manifest["rootFolder"].strip("/")
    manifest_files = {k.replace("\\", "/"): v.lower() for k, v in manifest["files"].items()}

    seen: set[str] = set()
    to_extract: list[tuple[zipfile.ZipInfo, str]] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if is_symlink(info):
                raise VerifyError(f"archive contains a symlink entry: {info.filename!r}")
            rel = validate_entry_name(info.filename, root)
            if not rel:
                continue
            key = rel.lower()
            if key in seen:
                raise VerifyError(f"archive contains duplicate entry: {rel!r}")
            seen.add(key)
            to_extract.append((info, rel))

        rels = {rel for _, rel in to_extract}
        # The ZIP may carry a copy of the manifest so the installed tree knows
        # its own file list; it can't hash itself, so pin it to the outer asset.
        if manifest_path is not None and "release-manifest.json" in rels:
            manifest_files.setdefault("release-manifest.json", sha256_file(manifest_path))
        extra = rels - set(manifest_files)
        if extra:
            raise VerifyError(f"archive contains files not in the manifest: {sorted(extra)[:5]}")
        missing = set(manifest_files) - rels
        if missing:
            raise VerifyError(f"archive is missing manifest files: {sorted(missing)[:5]}")

        total = sum(info.file_size for info, _ in to_extract)
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise VerifyError(f"archive decompresses to {total} bytes, over the "
                              f"{MAX_TOTAL_UNCOMPRESSED}-byte safety cap")

        out_root = (dest / root).resolve()
        dest_resolved = dest.resolve()
        for info, rel in to_extract:
            target = (out_root / rel).resolve()
            if dest_resolved not in target.parents and target != dest_resolved:
                raise VerifyError(f"extraction would escape staging: {rel!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    dst.write(chunk)

    for rel, expected in manifest_files.items():
        actual = sha256_file(out_root / rel)
        if actual != expected:
            raise VerifyError(f"hash mismatch after extraction for {rel}: "
                              f"expected {expected}, got {actual}")
    return out_root

def check_manifest_expectations(manifest: dict, expect_version: str | None,
                                expect_python: str | None,
                                expect_arch: str | None,
                                supported_protocols: set[int],
                                allow_dirty: bool) -> None:
    if expect_version and str(manifest["version"]) != expect_version:
        raise VerifyError(f"manifest version {manifest['version']!r} != release tag "
                          f"version {expect_version!r}")
    root = manifest["rootFolder"].strip("/")
    if root != f"valorant-scout-v{manifest['version']}":
        raise VerifyError(f"unexpected root folder {root!r}")
    if manifest["dirty"] and not allow_dirty:
        raise VerifyError("release was built from a dirty working tree")
    if expect_python and str(manifest["python"]["version"]) != expect_python:
        raise VerifyError(f"release requires Python {manifest['python']['version']!r}; "
                          f"this updater supports {expect_python!r}")
    if expect_arch and str(manifest["python"]["arch"]).lower() != expect_arch.lower():
        raise VerifyError(f"release architecture {manifest['python']['arch']!r}; "
                          f"this updater supports {expect_arch!r}")
    if supported_protocols and manifest["protocol"] not in supported_protocols:
        raise VerifyError(f"release protocol {manifest['protocol']} is not supported "
                          f"by this updater ({sorted(supported_protocols)})")
    version_rel = "VERSION"
    if version_rel not in {k.replace("\\", "/") for k in manifest["files"]}:
        raise VerifyError("manifest does not include the VERSION file")
    if "runtime.json" not in {k.replace("\\", "/") for k in manifest["files"]}:
        raise VerifyError("manifest does not include runtime.json")

def check_extracted_contract(out_root: Path, manifest: dict) -> None:
    try:
        runtime = json.loads((out_root / "runtime.json").read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise VerifyError(f"runtime.json inside archive is unreadable: {exc}") from exc
    if runtime.get("schemaVersion") != 1:
        raise VerifyError("runtime.json inside archive has an unsupported schema")
    if str(runtime.get("app", {}).get("version")) != str(manifest["version"]):
        raise VerifyError("runtime.json app.version does not match the release manifest")
    runtime_python = runtime.get("python", {})
    if (str(runtime_python.get("version")) != str(manifest["python"]["version"])
            or str(runtime_python.get("arch", "")).lower()
            != str(manifest["python"]["arch"]).lower()):
        raise VerifyError("runtime.json Python contract does not match the release manifest")
    if runtime.get("protocol", {}).get("version") != manifest["protocol"]:
        raise VerifyError("runtime.json protocol does not match the release manifest")

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, type=Path)
    ap.add_argument("--sums", required=True, type=Path)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--expect-version", default=None)
    ap.add_argument("--expect-python", default=None)
    ap.add_argument("--expect-arch", default=None)
    ap.add_argument("--supported-protocol", action="append", type=int, default=[])
    ap.add_argument("--allow-dirty", action="store_true",
                    help="development-only: accept an artifact built from a dirty tree")
    args = ap.parse_args(argv)

    try:
        check_asset_hash(args.zip, args.sums)
        check_asset_hash(args.manifest, args.sums)
        manifest = load_manifest(args.manifest)
        check_manifest_expectations(manifest, args.expect_version,
                                    args.expect_python, args.expect_arch,
                                    set(args.supported_protocol), args.allow_dirty)
        args.dest.mkdir(parents=True, exist_ok=True)
        out_root = verify_and_extract(args.zip, manifest, args.dest, args.manifest)
        check_extracted_contract(out_root, manifest)
        version_text = (out_root / "VERSION").read_text(encoding="utf-8-sig").strip()
        if version_text != str(manifest["version"]):
            raise VerifyError(f"VERSION inside archive is {version_text!r}, expected "
                              f"{manifest['version']!r}")
    except VerifyError as e:
        print(f"VS-UPDATE-001 {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — any unexpected failure must block the update
        print(f"VS-UPDATE-001 unexpected verification failure: {e}", file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, "root": str(out_root),
                      "version": manifest["version"],
                      "commit": manifest["commit"],
                      "fileCount": len(manifest["files"])}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
