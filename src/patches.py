"""Dataset-owned patch capture with the existing git-diff behavior as default."""

from pathlib import PurePosixPath


def patch_capture_command(workspace, artifacts):
    capture = getattr(workspace, "patch_capture_command", None)
    if callable(capture):
        return capture(artifacts)
    return ["bash", "-c", 'git diff > "$1"', "capture-patch",
            str(PurePosixPath(artifacts.execution) / "patch.diff")]


def capture_patch(workspace, artifacts, *, timeout=60):
    if not callable(getattr(workspace, "patch_capture_command", None)):
        result = workspace.execute(["git", "diff"], timeout=timeout)
        result.check_returncode()
        return result.stdout
    capture_timeout = getattr(workspace, "collect_timeout", timeout)
    result = workspace.execute(patch_capture_command(workspace, artifacts), timeout=capture_timeout + 5)
    result.check_returncode()
    # Preserve embedded CRLF in the original diff rather than universal-newline conversion.
    return (artifacts.host / "patch.diff").read_bytes().decode("utf-8")
