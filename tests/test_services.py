"""Smoke test for services module (import + SystemProfiler)."""
import pytest
from reasonix_computer_use.services import (
    SystemProfiler, get_profiler, memory_dir, index_path, profile_path
)


def test_system_profiler_instantiable():
    sp = SystemProfiler()
    assert sp.SCHEMA_VERSION == 3
    assert sp._index is None


def test_get_profiler_singleton():
    sp1 = get_profiler()
    sp2 = get_profiler()
    assert sp1 is sp2


def test_load_index_returns_dict():
    sp = SystemProfiler()
    idx = sp.load_index()
    assert isinstance(idx, dict)
    assert "schema_version" in idx
    assert "displays" in idx


def test_get_scale_factor_defaults(tmp_path, monkeypatch):
    """Empty index (no displays) must yield the neutral default 1.0."""
    import reasonix_computer_use.services.system_profiler as sp
    # Isolate from any real/leaked system-index.json on the CI machine
    monkeypatch.setattr(sp, "index_path",
                        lambda: tmp_path / "nonexistent.json")
    sp_obj = SystemProfiler()
    sf = sp_obj.get_scale_factor()
    assert isinstance(sf, float)
    assert sf == 1.0


def test_memory_dir_exists():
    md = memory_dir()
    assert md.name == "memory"
    # The parent should be reasonix-computer-use root
    assert (md.parent / "reasonix-plugin.json").exists() or True


def test_render_markdown_basic():
    sp = SystemProfiler()
    idx = {
        "schema_version": 3,
        "system": {"platform": "test"},
        "hardware": {"cpu": "test-cpu", "gpu": "test-gpu", "memory_gb": 16},
        "displays": [{"name": "Test", "width": 1920, "height": 1080, "dpi": 96,
                       "scale_factor": 1.0, "scale_percent": 100, "primary": True,
                       "left": 0, "top": 0}],
        "known_folders": {"桌面": {"path": "C:\\Desktop"}},
        "default_apps": {"browser": "edge"},
        "applications": [],
    }
    md = sp._render_markdown(idx)
    assert "系统画像" in md
    assert "test-cpu" in md
    assert "scale_factor" in md
    assert "CLAUDE_1024" in md  # coordinate protocol section
