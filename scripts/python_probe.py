import json
import platform
import struct
import sys

print(
    json.dumps(
        {
            "implementation": platform.python_implementation(),
            "version": "{}.{}.{}".format(*sys.version_info[:3]),
            "machine": platform.machine(),
            "bits": struct.calcsize("P") * 8,
            "executable": sys.executable,
            "prefix": sys.prefix,
            "basePrefix": sys.base_prefix,
            "isVenv": sys.prefix != sys.base_prefix,
        }
    )
)
