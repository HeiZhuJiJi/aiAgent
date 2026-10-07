"""HTTP API application factory."""

from importlib import import_module
from typing import Any

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    """Load the HTTP application lazily.

    Worker-only imports may need API contracts without constructing the entire
    FastAPI application. Eagerly importing the app here creates a cycle through
    storage model configuration modules.
    """
    if name not in __all__:
        raise AttributeError(name)
    application = import_module("enterprise_agent.api.app")
    # Importing the application submodule also installs that module as this
    # package''s app attribute. Cache both public exports afterwards so the
    # public import keeps returning the FastAPI instance, not the submodule.
    globals()["app"] = application.app
    globals()["create_app"] = application.create_app
    return globals()[name]
