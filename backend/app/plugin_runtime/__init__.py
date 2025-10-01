"""Runtime support utilities for dynamically loaded plugins.

Separated from the on-disk plugins/ directory so that the latter can be
mounted or replaced independently (e.g. via a Docker volume) without losing
core loader logic shipped with the backend image.
"""
