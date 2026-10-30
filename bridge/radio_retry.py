"""Run the pinned community bridge, retrying speakers that fail at startup.

The upstream SpeakerBridge.run() returns permanently when its initial UPnP
request fails. A brief Wi-Fi outage during boot otherwise leaves the container
running with no active speaker threads.
"""

import importlib.util
import sys
import time


# /bridge.py normally starts with / on sys.path when executed directly.
sys.path.insert(0, "/")
spec = importlib.util.spec_from_file_location("soundtouch_bridge", "/bridge.py")
if spec is None or spec.loader is None:
    raise RuntimeError("Cannot load /bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

# A connect started while Wi-Fi is down must not stay bound to the Ethernet
# source address after Wi-Fi comes back.
bridge.websocket.setdefaulttimeout(5)

original_run = bridge.SpeakerBridge.run


def retrying_run(self):
    while self.active:
        try:
            original_run(self)
        except Exception as exc:
            print(f"[{self.host}] bridge thread crashed: {exc}", flush=True)
        if self.active:
            print(f"[{self.host}] retrying bridge initialization in 5s...", flush=True)
            time.sleep(5)


bridge.SpeakerBridge.run = retrying_run
bridge.main()
