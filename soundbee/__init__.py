"""
Compatibility shim for SoundBee -> Batuque transition.
All core modules and functionality have migrated to the `batuque` package.
"""
import batuque
from batuque import *
__version__ = batuque.__version__
