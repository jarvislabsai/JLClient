from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jarvislabs.transport import Transport


@pytest.fixture()
def mock_transport():
    return MagicMock(spec=Transport)
