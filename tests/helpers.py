from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
from collections.abc import Awaitable
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, TypeVar, cast, overload

from typing_extensions import ParamSpec

if TYPE_CHECKING:
    from types import TracebackType

T = TypeVar("T")
P = ParamSpec("P")


def purge_module(module_names: list[str], path: str | Path) -> None:
    for name in module_names:
        if name in sys.modules:
            del sys.modules[name]
    Path(importlib.util.cache_from_source(path)).unlink(missing_ok=True)  # type: ignore[arg-type]


class _ContextManagerWrapper:
    def __init__(self, cm: AbstractContextManager[T]) -> None:
        self._cm = cm

    async def __aenter__(self) -> T:  # pyright: ignore
        return self._cm.__enter__()  # type: ignore[return-value] # pyright: ignore

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        return self._cm.__exit__(exc_type, exc_val, exc_tb)


@overload
async def maybe_async(obj: Awaitable[T]) -> T: ...


@overload
async def maybe_async(obj: T) -> T: ...


async def maybe_async(obj: Awaitable[T] | T) -> T:
    return cast(T, await obj) if inspect.isawaitable(obj) else cast(T, obj)  # type: ignore[redundant-cast]


def maybe_async_cm(obj: AbstractContextManager[T] | AbstractAsyncContextManager[T]) -> AbstractAsyncContextManager[T]:
    if isinstance(obj, AbstractContextManager):
        return cast(AbstractAsyncContextManager[T], _ContextManagerWrapper(obj))
    return obj


def wrap_sync(fn: Callable[P, T]) -> Callable[P, Awaitable[T]]:
    if inspect.iscoroutinefunction(fn):
        return fn

    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        return await asyncio.get_running_loop().run_in_executor(None, partial(fn, *args, **kwargs))

    return wrapped


class NoValue:
    """A fake "Empty class"""


async def anext_(iterable: Any, default: Any = NoValue, *args: Any) -> Any:  # pragma: no cover
    """Return the next item from an async iterator.

    Args:
        iterable: An async iterable.
        default: An optional default value to return if the iterable is empty.
        *args: The remaining args
    Return:
        The next value of the iterable.

    Raises:
        TypeError: The iterable given is not async.

    This function will return the next value form an async iterable. If the
    iterable is empty the StopAsyncIteration will be propagated. However, if
    a default value is given as a second argument the exception is silenced and
    the default value is returned instead.
    """
    has_default = bool(not isinstance(default, NoValue))
    try:
        return await iterable.__anext__()

    except StopAsyncIteration as exc:
        if has_default:
            return default

        raise StopAsyncIteration from exc
