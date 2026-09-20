"""Workspace model transport around the existing raw HTTP usage recorder."""

from contextlib import contextmanager
import json
from pathlib import Path, PurePosixPath
import platform
import socket
import subprocess
import time
from urllib.parse import urlsplit
from uuid import uuid4

from .byte_relay import relay


def validate_channel(runtime, *, supported=False):
    if runtime.get("network") == "host":
        return
    channel = runtime.get("model_channel")
    if (not supported or runtime.get("network") != "none" or not isinstance(channel, dict)
            or channel.get("kind") != "unix_socket"):
        raise ValueError("model recording requires host networking or a dataset-supported unix_socket channel")
    if platform.system() != "Linux" or not hasattr(socket, "AF_UNIX"):
        raise ValueError("isolated model channel requires a Linux host with Unix sockets and local Docker")
    python = channel.get("python")
    if not isinstance(python, str) or not PurePosixPath(python).is_absolute():
        raise ValueError("model_channel.python must be an absolute prepared in-image Python 3 path")


@contextmanager
def recording_model_channel(workspace, artifacts, upstream_base_url, *, channel_name=None, **options):
    from .usage_proxy import recording_proxy

    directory = artifacts.host / "api-records"
    channel_artifacts = artifacts
    if channel_name is not None:
        if not isinstance(channel_name, str) or not channel_name or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in channel_name):
            raise ValueError("channel_name must be a simple unique identifier")
        directory = artifacts.host / "auxiliary-records" / channel_name
        from .interfaces import ArtifactDirectory
        state = artifacts.host / "auxiliary-channel-state" / channel_name
        state.mkdir(parents=True, exist_ok=False)
        channel_artifacts = ArtifactDirectory(state,
            str(PurePosixPath(artifacts.execution) / "auxiliary-channel-state" / channel_name))
    identity = getattr(workspace, "usage_identity", {})
    options["attribution"] = {**options.get("attribution", {}), **identity}
    snapshot = getattr(workspace, 'snapshot', None)
    if snapshot is not None and channel_name is None:
        options['before_request'] = snapshot
    with recording_proxy(directory, upstream_base_url, **options) as endpoint:
        expose = getattr(workspace, "expose_model_endpoint", None)
        if callable(expose):
            with expose(endpoint, channel_artifacts) as local:
                yield local
        else:
            yield endpoint


@contextmanager
def isolated_endpoint(workspace, endpoint, artifacts):
    upstream = urlsplit(endpoint)
    if upstream.scheme != "http" or upstream.hostname != "127.0.0.1" or not upstream.port:
        raise ValueError("isolated channel accepts only the local recording proxy")
    socket_name = uuid4().hex + ".sock"
    host_socket = Path(workspace.channel_directory) / socket_name
    target_socket = "/tokenana/model-channel/" + socket_name
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    process = None
    stop = artifacts.host / "model-channel.stop"
    ready = artifacts.host / "model-channel.ready.json"
    target = PurePosixPath(artifacts.execution)
    try:
        listener.bind(str(host_socket))
        # The directory is isolated to this workspace; allow its image's user to connect.
        host_socket.chmod(0o666)
        listener.listen(128)
        with relay(listener, lambda: socket.create_connection(("127.0.0.1", upstream.port), timeout=5)):
            argv = ["docker", "exec", "--workdir", workspace.root, workspace.container,
                    workspace.channel_python, "-B", "/tokenana/byte_relay.py", target_socket,
                    str(target / ready.name), str(target / stop.name)]
            with (artifacts.host / "model-channel.log").open("w") as log:
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 15
                    while not ready.exists():
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise ValueError("container model relay did not become ready; see model-channel.log")
                        time.sleep(0.05)
                    port = json.loads(ready.read_text())["port"]
                    if type(port) is not int or not 0 < port < 65536:
                        raise ValueError("invalid container model relay port")
                    yield f"http://127.0.0.1:{port}{upstream.path}"
                finally:
                    stop.touch()
                    try:
                        returncode = process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        raise RuntimeError("model relay did not stop; workspace container cleanup required")
                    if returncode:
                        raise RuntimeError(f"container model relay exited with code {returncode}; see model-channel.log")
    finally:
        listener.close()
        host_socket.unlink(missing_ok=True)
