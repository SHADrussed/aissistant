"""Prepare mounted volume before dropping privileges; never inspect credentials."""

import os
import sys
from pathlib import Path

if os.getuid() == 0:
    data = Path("/data")
    data.mkdir(exist_ok=True, mode=0o700)
    os.chown(data, 10001, 10001)
    os.chmod(data, 0o700)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
os.umask(0o077)
# Synthetic state also lives on the writable volume, separate from live data.
os.environ.setdefault("SYNTHETIC_DATABASE_URL", "sqlite:////data/synthetic.db")
os.execvp(sys.argv[1], sys.argv[1:])
