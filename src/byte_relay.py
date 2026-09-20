"""Standard-library fixed-destination byte relay; also mounted into agent images."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import socket
import threading
import time


@contextmanager
def relay(listener, connect):
    """No HTTP parsing, request rewriting, destination selection or retries."""
    stopped = threading.Event()
    lock = threading.Lock()
    active, workers = set(), []

    def forward(client):
        upstream = None
        try:
            upstream = connect()
            with lock:
                if stopped.is_set():
                    return
                active.add(upstream)
            # HTTP deadlines belong to the existing recorder, not this byte transport.
            client.settimeout(None)
            upstream.settimeout(None)
            peers = {client: upstream, upstream: client}
            while peers and not stopped.is_set():
                readable, _, _ = select.select(list(peers), [], [], 0.2)
                for source in readable:
                    body = source.recv(65536)
                    if body:
                        peers[source].sendall(body)
                    else:
                        peers[source].shutdown(socket.SHUT_WR)
                        del peers[source]
        except (OSError, ValueError):
            # Native HTTP recorder/agent retains transport failure evidence.
            pass
        finally:
            for connection in (client, upstream):
                if connection is not None:
                    connection.close()
                    with lock:
                        active.discard(connection)

    def accept():
        listener.settimeout(0.2)
        while not stopped.is_set():
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with lock:
                active.add(client)
            worker = threading.Thread(target=forward, args=(client,), daemon=True)
            workers.append(worker)
            worker.start()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        listener.close()
        thread.join(timeout=2)
        with lock:
            connections = list(active)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        deadline = time.monotonic() + 6
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))


def main(socket_path, ready_path, stop_path):
    def connect():
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(5)
        try:
            connection.connect(socket_path)
        except BaseException:
            connection.close()
            raise
        return connection

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Verify the mounted socket is connectable before announcing readiness.
        # No HTTP request or model call is sent by this transport handshake.
        with connect():
            pass
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        with relay(listener, connect):
            path = Path(ready_path)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"port": listener.getsockname()[1], "pid": os.getpid()}))
            temporary.replace(path)
            while not Path(stop_path).exists():
                time.sleep(0.1)
    finally:
        listener.close()


if __name__ == "__main__":
    import sys
    main(*sys.argv[1:])
