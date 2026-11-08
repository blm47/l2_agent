import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from hid_pilot.api_server import handler_for


def test_local_api_auth_schema_and_script_identifier():
    runtime = Mock()
    runtime.status.return_value = {"protocol": 1, "running": False}
    runtime.start.return_value = {"protocol": 1, "running": True}
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(runtime, "x" * 32))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    url = f"http://127.0.0.1:{server.server_port}/v1/"

    def call(path, data=None, token="x" * 32):
        request = Request(url + path, data=json.dumps(data).encode() if data is not None else None,
                          headers={"Authorization": "Bearer " + token})
        return opener.open(request, timeout=2)

    try:
        with pytest.raises(HTTPError) as failure:
            call("status", token="bad")
        assert failure.value.code == 401
        with call("status") as response:
            assert json.load(response)["protocol"] == 1
        with pytest.raises(HTTPError) as failure:
            call("start", {"script_id": "../../outside"})
        assert failure.value.code == 409
        runtime.start.assert_not_called()
        with call("start", {"script_id": "farmer-healer.duo"}):
            pass
        runtime.start.assert_called_once_with("farmer-healer.duo")
        with pytest.raises(HTTPError):
            call("camera", {"session_id": "a" * 32, "frame_id": 1, "target_epoch": 1,
                             "direction": "left", "dx": 9999})
        runtime.camera.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
