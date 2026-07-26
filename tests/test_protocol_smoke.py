"""Pytest tests for protocol coordinate conversion."""
import pytest
from reasonix_computer_use.protocol import (
    CoordinateSpace, CoordinateConverter, NormalizedCoord,
    ScreenSnapshot,
)


class TestCoordinateConverter:
    """Coordinate conversion: normalized spaces ↔ physical pixels."""

    def test_claude_1024_to_physical_center(self):
        converter = CoordinateConverter(scale_factor=1.0, display_width=1920, display_height=1080)
        nc = NormalizedCoord(x=512, y=384, space=CoordinateSpace.CLAUDE_1024)
        px, py = converter.to_physical(nc)
        assert (px, py) == (960, 540)

    def test_gemini_1000_to_physical_center(self):
        converter = CoordinateConverter(scale_factor=1.0, display_width=1920, display_height=1080)
        nc = NormalizedCoord(x=500, y=500, space=CoordinateSpace.GEMINI_1000)
        px, py = converter.to_physical(nc)
        assert (px, py) == (960, 540)

    def test_pixel_passthrough(self):
        converter = CoordinateConverter(scale_factor=1.0, display_width=1920, display_height=1080)
        nc = NormalizedCoord(x=100, y=200, space=CoordinateSpace.PIXEL)
        px, py = converter.to_physical(nc)
        assert (px, py) == (100, 200)

    def test_reverse_conversion(self):
        converter = CoordinateConverter(scale_factor=1.0, display_width=1920, display_height=1080)
        nc = converter.from_physical(960, 540, CoordinateSpace.CLAUDE_1024)
        assert nc.x == 512 and nc.y == 384
        assert nc.space == CoordinateSpace.CLAUDE_1024

    def test_scale_factor_conversion(self):
        converter = CoordinateConverter(scale_factor=1.5, display_width=1920, display_height=1080)
        nc = NormalizedCoord(x=512, y=384, space=CoordinateSpace.CLAUDE_1024)
        px, py = converter.to_physical(nc)
        assert (px, py) == (1440, 810)

    def test_claude_1024_bounds(self):
        """Corners of the CLAUDE_1024 space (canonical 1024×768, display 1920×1080)."""
        converter = CoordinateConverter(scale_factor=1.0, display_width=1920, display_height=1080)
        # Top-left
        assert converter.to_physical(NormalizedCoord(0, 0, CoordinateSpace.CLAUDE_1024)) == (0, 0)
        # Bottom-right: (1023, 1023) maps roughly to (1918, 1439) because CLAUDE_1024 canonical Y=768
        px, py = converter.to_physical(NormalizedCoord(1023, 1023, CoordinateSpace.CLAUDE_1024))
        assert 1915 <= px <= 1920 and 1435 <= py <= 1440

    def test_claude_1024_reject_out_of_range(self):
        with pytest.raises(ValueError):
            NormalizedCoord(x=1024, y=0, space=CoordinateSpace.CLAUDE_1024)
        with pytest.raises(ValueError):
            NormalizedCoord(x=0, y=-1, space=CoordinateSpace.CLAUDE_1024)

    def test_gemini_1000_reject_out_of_range(self):
        with pytest.raises(ValueError):
            NormalizedCoord(x=1000, y=0, space=CoordinateSpace.GEMINI_1000)

    def test_element_ref_requires_ref(self):
        with pytest.raises(ValueError, match="ref"):
            NormalizedCoord(x=0, y=0, space=CoordinateSpace.ELEMENT_REF)

    def test_element_ref_cannot_convert_to_physical(self):
        converter = CoordinateConverter()
        with pytest.raises(ValueError, match="ELEMENT_REF"):
            converter.to_physical(NormalizedCoord(x=0, y=0, space=CoordinateSpace.ELEMENT_REF, ref="e1"))

    def test_window_rect_offset(self):
        """When a window is not at (0,0), coords should offset."""
        converter = CoordinateConverter(scale_factor=1.0, display_width=1920, display_height=1080)
        # Window at (100, 50, 1100, 818) — 1000×768 area
        window_rect = (100, 50, 1100, 818)
        nc = NormalizedCoord(x=512, y=384, space=CoordinateSpace.CLAUDE_1024)
        px, py = converter.to_physical(nc, window_rect=window_rect)
        assert px == 100 + 500  # 100 offset + 500 (half of 1000)
        assert py == 50 + 384   # 50 offset + 384 (half of 768)


class TestScreenSnapshot:
    """ScreenSnapshot serialization and element lookup."""

    def test_roundtrip(self):
        snap = ScreenSnapshot(
            revision=1,
            window_id="123",
            source="precision",
            width=1920,
            height=1080,
            scale_factor=1.5,
        )
        d = snap.to_dict()
        snap2 = ScreenSnapshot.from_dict(d)
        assert snap2.revision == 1
        assert snap2.source == "precision"
        assert snap2.scale_factor == 1.5

    def test_input_signals(self):
        snap = ScreenSnapshot(revision=2, window_id="456", source="vision",
                              input_ready=True, input_typed=False, input_submitted=False)
        d = snap.to_dict()
        assert d["input_ready"] is True
        snap2 = ScreenSnapshot.from_dict(d)
        assert snap2.input_ready is True

    def test_blocked_state(self):
        snap = ScreenSnapshot(revision=3, window_id="789", source="precision",
                              blocked=True, blocked_reason="access denied")
        d = snap.to_dict()
        assert d["blocked"] is True
        snap2 = ScreenSnapshot.from_dict(d)
        assert snap2.blocked_reason == "access denied"


class TestNormalizedCoord:
    """NormalizedCoord validation and serialization."""

    def test_to_dict_with_ref(self):
        nc = NormalizedCoord(x=100, y=200, space=CoordinateSpace.CLAUDE_1024, ref="btn_ok")
        d = nc.to_dict()
        assert d == {"x": 100, "y": 200, "space": "CLAUDE_1024", "ref": "btn_ok"}

    def test_from_dict_defaults(self):
        nc = NormalizedCoord.from_dict({"x": 42, "y": 84})
        assert nc.x == 42 and nc.y == 84
        assert nc.space == CoordinateSpace.CLAUDE_1024
        assert nc.ref is None
