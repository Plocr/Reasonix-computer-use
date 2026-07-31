"""Tests for the perception layer (precision + vision + router)."""
import pytest
from reasonix_computer_use.perception import (
    PerceptionProvider,
    PerceptionRouter,
    WindowsUIAPrecision,
    EasyOCRVision,
)
from reasonix_computer_use.perception.precision.macos_axapi import MacOSAXAPIPrecision
from reasonix_computer_use.perception.precision.linux_atspi import LinuxATSPI2Precision
from reasonix_computer_use.perception.vision.easy_ocr import EasyOCRVision as _EasyOCR
from reasonix_computer_use.protocol import ScreenSnapshot


class TestPerceptionRouter:
    """Verify the perception router is constructable and returns expected structure."""

    def test_router_instantiable(self):
        router = PerceptionRouter()
        assert router is not None

    def test_router_properties(self):
        router = PerceptionRouter()
        # On Windows, precision should be available (UIA)
        import sys
        if sys.platform == "win32":
            assert router.precision_available is True
        assert isinstance(router.vision_available, bool)

    def test_router_observe_does_not_crash(self):
        """Basic smoke test: observe() returns a ScreenSnapshot without crashing."""
        router = PerceptionRouter()
        snapshot = router.observe(max_elements=10)
        assert isinstance(snapshot, ScreenSnapshot)
        # Either we got elements, or blocked is True
        assert snapshot.elements or snapshot.blocked


class TestPerceptionProviders:
    """Verify provider interface conformance."""

    def test_windows_uia_available(self):
        import sys
        wp = WindowsUIAPrecision()
        if sys.platform == "win32":
            assert wp.available is True
            assert wp.source == "precision"
        else:
            assert wp.available is False

    def test_macos_axapi_not_available_on_windows(self):
        mp = MacOSAXAPIPrecision()
        assert mp.available is False
        assert mp.source == "precision"

    def test_linux_atspi_availability(self):
        import sys
        lp = LinuxATSPI2Precision()
        if sys.platform == "linux":
            # On Linux CI (xvfb + python3-gi) the provider is available;
            # on a Wayland session it is not.
            assert lp.available is True
        else:
            assert lp.available is False
        assert lp.source == "precision"

    def test_easy_ocr_source(self):
        import pytest
        pytest.skip("PaddleOCR removed in beta.3")
        ocr = _EasyOCR()
        assert ocr.source == "vision"


class TestScreenSnapshotFlow:
    """End-to-end test: router → snapshot → element lookup."""

    def test_snapshot_structure(self):
        router = PerceptionRouter()
        snapshot = router.observe(max_elements=10)
        assert snapshot.revision is not None
        assert snapshot.window_id
        assert snapshot.source in ("precision", "vision", "unavailable")
        assert snapshot.width >= 0
        assert snapshot.height >= 0

        if snapshot.elements:
            el = snapshot.elements[0]
            assert el.id
            assert isinstance(el.role, str)
            assert len(el.bbox) == 4
