"""Runs only inside the prepared verifier container, using the original Pier code."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from pier.models.task.task import Task
from pier.models.task.config import TaskOS
from pier.models.trial.paths import EnvironmentPaths, TrialPaths
from pier.verifier.verifier import Verifier


class LocalVerifierEnvironment:
    """The Verifier's environment interface, bound to this disposable container."""

    env_paths = EnvironmentPaths()
    task_os = TaskOS.LINUX
    capabilities = SimpleNamespace(mounted=True)

    async def exec(self, command, env=None, user=None):
        if user not in (None, "root", 0):
            raise ValueError("the prepared verifier worker supports root only")
        process = await asyncio.create_subprocess_exec(
            "/bin/bash", "-c", command, cwd="/app", env={**os.environ, **(env or {})},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await process.communicate()
        return SimpleNamespace(return_code=process.returncode,
                               stdout=out.decode("utf-8", errors="replace"),
                               stderr=err.decode("utf-8", errors="replace"))


async def main():
    if os.geteuid() != 0:
        raise ValueError("the prepared verifier image must run the worker as root")
    task = Task(Path("/tokenana/task"))
    if task.config.verifier.user not in (None, "root", 0):
        raise ValueError("unsupported verifier user")
    paths = TrialPaths(Path("/logs"))
    result = await Verifier(task, paths, LocalVerifierEnvironment(), skip_tests_upload=True).verify()
    output = Path("/logs/pier-result.json")
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(result.model_dump(), allow_nan=False), encoding="utf-8")
    temporary.replace(output)


if __name__ == "__main__":
    asyncio.run(main())
