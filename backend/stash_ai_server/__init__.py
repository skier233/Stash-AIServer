__all__ = ["__version__"]

# Derive the package version from installed distribution metadata when
# available (this will reflect the version embedded in the built wheel /
# the Git tag used during CI). When running from a bare source checkout
# (dev), fall back to a local dev version string.
try:
	from importlib.metadata import version, PackageNotFoundError
	try:
		__version__ = version("stash-ai-server")
	except PackageNotFoundError:
		__version__ = "0.0.0+local"
except Exception:
	# Extremely defensive fallback for very old runtimes or unexpected failures
	__version__ = "0.0.0+local"

# Makes backend/stash_ai_server a package for test imports
