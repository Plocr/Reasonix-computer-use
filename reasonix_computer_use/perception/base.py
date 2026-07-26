"""
Perception provider — abstract base class for screen observation.

Every provider returns a ScreenSnapshot containing structured element data:
element IDs, bounding boxes, text, and a11y metadata.  The vision layer only
outputs coordinates; the host Agent (VLM/LLM) makes all action decisions.
"""

from __future__ import annotations

import abc
from typing import Optional

from ..protocol import ScreenSnapshot


class PerceptionProvider(abc.ABC):
    """Observe a window and return a structured ScreenSnapshot.

    Subclasses implement precision (UIA, AXAPI, AT-SPI2) or vision (OCR) layers.
    """

    @abc.abstractmethod
    def observe(
        self,
        window_id: Optional[str] = None,
        max_elements: int = 80,
    ) -> ScreenSnapshot:
        """Observe a window and return structured element data.

        Args:
            window_id: Platform window identifier. None = foreground window.
            max_elements: Maximum number of elements to return.

        Returns:
            ScreenSnapshot with element IDs, bboxes, text, and a11y source.
        """
        ...

    @property
    @abc.abstractmethod
    def source(self) -> str:
        """Return the layer name: 'precision' or 'vision'."""
        ...

    @property
    @abc.abstractmethod
    def available(self) -> bool:
        """Whether this provider is usable on the current system."""
        ...
