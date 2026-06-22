"""Compatibility imports for older page modules.

All implementations are local to TradePush. New code should import
``tradepush.collectors.local`` directly.
"""

from tradepush.collectors.common import latest_file, read_csv_safe
from tradepush.collectors.local import *  # noqa: F401,F403
