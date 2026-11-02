"""Register the performance analytics provider through capability entry points."""

from importlib.metadata import version as _distribution_version

from nlalpha.core.capabilities.manifest import ProviderManifest
from nlalpha.core.capabilities.plugin.protocol import BaseCapabilityPlugin


class PerformancePlugin(BaseCapabilityPlugin):
    """Expose the performance analytics capability provider."""

    plugin_name = "nlalpha_performance"
    plugin_version = _distribution_version("nlalpha_performance")


    def __init__(self) -> None:
        self._provider = None

    def is_available(self) -> bool:
        """Check whether the empyrical dependency is installed."""
        try:
            __import__("empyrical")
            return True
        except ImportError:
            return False

    def get_provider(self):
        """Return the cached performance analytics provider."""
        if self._provider is None:
            from .provider import PerformanceProvider
            self._provider = PerformanceProvider()
        return self._provider

    def get_manifest(self) -> dict:
        """Return the provider runtime manifest."""
        if self.is_available():
            provider = self.get_provider()
            manifest = provider.manifest().to_dict()
            manifest["domains"] = ["performance"]
            return manifest
        return ProviderManifest(
            provider_id="performance",
            provider_name="PerformancePlugin",
            available=False,
            unavailable_reason="empyrical-reloaded not installed",
            domains=["performance"],
        ).to_dict()

    def get_helpers(self) -> list[dict]:
        """Return the capability helper declarations."""
        return []

    def get_prompt_path(self) -> str | None:
        """Return the optional capability prompt path."""
        return None
