"""Empty dashboard fixtures own their HOME for the server's entire lifetime."""
import os
import tempfile
from unittest.mock import patch

import dashboard


def make_server(root, port=0):
    home = tempfile.TemporaryDirectory(prefix="sq-dashboard-home-")
    try:
        with patch.dict(os.environ, {"HOME": home.name}):
            server, token = dashboard.make_server(root, port)
    except BaseException:
        home.cleanup()
        raise
    close = server.server_close

    def close_with_home():
        try:
            close()
        finally:
            home.cleanup()

    server.server_close = close_with_home
    return server, token
