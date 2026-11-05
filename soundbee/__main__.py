"""
Compatibility entrypoint for SoundBee -> Batuque transition.
"""
import sys
from batuque.__main__ import run_entrypoint

if __name__ == "__main__":
    sys.exit(run_entrypoint() or 0)
