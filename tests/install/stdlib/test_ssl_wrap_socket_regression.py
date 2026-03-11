"""Direct in-process reproducer for the SSL wrap_socket crash."""

import socket
import ssl


def test_install_component_ssl_wrap_socket_crash_reproducer(runner):
    """This currently segfaults during runner.record(work)."""

    def work():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ctx = ssl.create_default_context()
        wrapped = ctx.wrap_socket(
            s,
            server_hostname="example.com",
            do_handshake_on_connect=False,
        )
        wrapped.close()
        s.close()

    runner.record(work)
