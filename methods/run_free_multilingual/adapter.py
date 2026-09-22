"""Static multilingual repair with the same native-log accounting as run_free."""

from methods.run_free.adapter import RunFree


class RunFreeMultilingual(RunFree):
    version = "run-free-multilingual-v1"
    task_languages = frozenset({"go", "typescript", "javascript", "rust"})

    def build_prompt(self, task, *, allow_submission_git=False):
        return f"""You are a code repair expert.

## Repository Information
- Repository: {task.repo}
- Base Commit: {task.base_commit}

## Problem Description
{task.problem_statement}

## EXECUTION MODE - ZERO EXECUTION
Solve the task through static reading, reasoning and source editing.
File reading, search and file operations are allowed.
Git operations are allowed only to inspect changes and satisfy the task's required branch and commit submission.

## What You CANNOT Do
- Execute tests, programs, examples, benchmarks or arbitrary project code in any language.
- Compile, build, bundle, type-check, lint or run formatters and project scripts.
- Install, download or update dependencies or toolchains.
This includes go test/run/build, node, bun, deno, npm/pnpm/yarn scripts,
tsc, jest, vitest, cargo test/run/build/check, rustc and Python execution.
Do not bypass these restrictions through another interpreter, a Git hook or a script.

## Debugging Strategy
Read with purpose. Before opening a file, know what you are looking for.
Reason about the root cause from the source and apply the fix using editing tools.
Do not execute code to validate your reasoning.

## Submission
Make real source changes; a textual description alone is not a patch.
Follow the dataset delivery instructions below for Git commits and patch collection.
"""
