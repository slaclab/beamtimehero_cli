"""Tool executor — dispatches tool calls to handler implementations.

Returns ``(result_text, images_b64)`` for each tool invocation.

DISPATCH is keyed by ``(tree, ..., name)`` tuples so the same leaf name
can exist under multiple branches (e.g. ``("spec-file", "list_scans")``
and ``("s3df", "list_scans")``). Callers pass the tree along with the
name — a 1-tuple like ``("tool",)`` for top-level branches, or longer
like ``("s3df", "psql")`` for nested branches.

``execute_tool`` is the default executor over the library's whole
dispatch table. ``make_executor`` builds one over any table, with
optional hooks — that is how a restricted agent surface gets a dispatch
table holding only the tools it carries, and a motor allow-list that
cannot be bypassed by calling the executor directly. The envelopes are
produced in one place, so every executor answers an unknown tool and a
failing tool with the identical string.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional, Protocol

logger = logging.getLogger(__name__)


TreePath = tuple[str, ...]

#: Runs before dispatch. Returning a string aborts the call and that
#: string becomes the result text — so a guard returns its own error
#: envelope. Returning ``None`` lets the call through.
BeforeHook = Callable[[TreePath, str, dict], Optional[str]]

#: Runs after a successful dispatch. Returning a string replaces the
#: result text; returning ``None`` leaves it alone, which is what an
#: observer-only hook (e.g. capturing a scan number) does.
AfterHook = Callable[[TreePath, str, str], Optional[str]]


class Executor(Protocol):
    """What every executor in this codebase looks like.

    Deliberately the same shape as ``execute_tool`` so anything holding
    one can be handed the other.
    """

    def __call__(
        self,
        tree: TreePath | str,
        name: str,
        arguments: dict,
    ) -> tuple[str, list[str]]:
        ...


def make_executor(
    dispatch: dict,
    *,
    before: "tuple[BeforeHook, ...] | list[BeforeHook]" = (),
    after: "tuple[AfterHook, ...] | list[AfterHook]" = (),
) -> Executor:
    """Build an executor over ``dispatch``, with optional hooks.

    ``dispatch`` is held by reference, so a table that is mutated in
    place (as ``tools_core.DISPATCH`` is by ``register_handlers``) stays
    current without rebuilding the executor.
    """
    before = tuple(before)
    after = tuple(after)

    def _execute(
        tree: TreePath | str,
        name: str,
        arguments: dict,
    ) -> tuple[str, list[str]]:
        key_path = (tree,) if isinstance(tree, str) else tuple(tree)
        key = key_path + (name,)
        fn = dispatch.get(key)
        if fn is None:
            return f"Unknown tool: {'/'.join(key)}", []
        args = arguments or {}
        for hook in before:
            refusal = hook(key_path, name, args)
            if refusal is not None:
                return refusal, []
        try:
            text, imgs = fn(args)
        except Exception as e:
            logger.error("Tool %s failed: %s", "/".join(key), e, exc_info=True)
            return f"Tool error ({'/'.join(key)}): {e}", []
        for hook in after:
            replaced = hook(key_path, name, text)
            if replaced is not None:
                text = replaced
        return text, list(imgs or [])

    return _execute


_DEFAULT_EXECUTOR: Executor | None = None


def _default_executor() -> Executor:
    """The executor over the library's own dispatch table, built once.

    Built lazily because importing ``tools_core`` pulls in matplotlib and
    the science stack, and because ``DISPATCH`` is mutated in place — so
    one executor stays correct for the process's lifetime. An import
    failure is not cached: it is usually a missing optional dependency
    that a later call may not need.
    """
    global _DEFAULT_EXECUTOR
    if _DEFAULT_EXECUTOR is not None:
        return _DEFAULT_EXECUTOR
    try:
        from beamtimehero_cli.tool_catalog.tools_core import DISPATCH
    except Exception:
        return make_executor({})
    _DEFAULT_EXECUTOR = make_executor(DISPATCH)
    return _DEFAULT_EXECUTOR


def execute_tool(
    tree: tuple[str, ...] | str,
    name: str,
    arguments: dict,
) -> tuple[str, list[str]]:
    """Execute a tool by ``(tree, name)`` with arguments.

    ``tree`` may be a single string (single-segment branch) or a tuple
    of segments (for nested branches).
    """
    return _default_executor()(tree, name, arguments)
