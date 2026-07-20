__all__ = ["load_catalog", "CatalogError"]


def __getattr__(name: str):
    if name in __all__:
        from .catalog import CatalogError, load_catalog  # noqa: F401
        import sys
        mod = sys.modules[__name__]
        # Cache to avoid repeated lazy resolution
        mod.load_catalog = load_catalog
        mod.CatalogError = CatalogError
        return mod.__dict__[name]
    raise AttributeError(f"module 'wheatley_installer' has no attribute {name!r}")
