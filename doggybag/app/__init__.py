"""Toolkit-neutral application layer: controller, view models, view port."""

from .view import NullView, ViewPort

_CONTROLLER_NAMES = ("AppController", "BatchItem", "BatchPromptPolicy", "BatchRun")


def __getattr__(name):
    # Lazy so the headless CLI can import ``doggybag.app.workflow`` without
    # loading the GUI controller.
    if name in _CONTROLLER_NAMES:
        from . import controller

        return getattr(controller, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AppController",
    "BatchItem",
    "BatchPromptPolicy",
    "BatchRun",
    "NullView",
    "ViewPort",
]
