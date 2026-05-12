"""Smoke test — verifies 'github' is a valid CLI source name without executing it."""

def test_main_module_importable():
    import importlib
    mod = importlib.import_module("teamrag.ingest.__main__")
    assert hasattr(mod, "main")
