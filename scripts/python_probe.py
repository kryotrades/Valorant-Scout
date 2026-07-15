"""python_probe.py — print this interpreter's identity as one JSON line.

Run by the installer/launcher against every candidate python.exe so runtime
acceptance is based on what the executable actually is, never on its name.
A Store alias stub, broken shim or wrong-arch build either fails to run or
prints an identity that the caller rejects. Stdlib only; must work on any
Python that can start at all.
"""
import json
import platform
import struct
import sys

print(json.dumps({
    "implementation": platform.python_implementation(),
    "version": "%d.%d.%d" % sys.version_info[:3],
    "machine": platform.machine(),
    "bits": struct.calcsize("P") * 8,
    "executable": sys.executable,
    "prefix": sys.prefix,
    "basePrefix": sys.base_prefix,
    "isVenv": sys.prefix != sys.base_prefix,
}))
