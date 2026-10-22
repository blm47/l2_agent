from unittest.mock import Mock

import numpy as np

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.chat_preview import latest_chat_preview
from hid_pilot.system_chat import SYSTEM_CHAT_ENABLED, SystemChatOCR, ChatMessage


def test_chat_is_disabled_even_for_old_enabled_profiles():
    assert not SYSTEM_CHAT_ENABLED
    profile = BarProfile(width=100, height=100, system_chat_enabled=True,
                         system_chat=BarBox(x1=0, y1=0, x2=1, y2=1))
    numbers = Mock()
    reader = SystemChatOCR(profile, numbers)
    reader.chat_events.append(ChatMessage("sitting_block", "old event", 1.))
    assert reader.take_chat_events() == []
    reader.read_chat(np.zeros((100, 100, 3), dtype=np.uint8), 1.)
    numbers.detect.assert_not_called()
    assert not reader.chat_ready()
    assert reader.take_chat_events() == []
    assert "временно отключено" in latest_chat_preview().status
