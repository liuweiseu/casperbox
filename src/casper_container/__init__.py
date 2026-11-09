"""casper: build and run CASPER toolflow development containers with rootless podman."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("casper-container")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0+unknown"
