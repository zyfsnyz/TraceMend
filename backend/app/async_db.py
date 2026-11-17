from collections.abc import Callable
from functools import partial
from typing import ParamSpec, TypeVar

import anyio

P = ParamSpec("P")
R = TypeVar("R")


async def run_db(func: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
    """Run synchronous SQL work without blocking an async request's event loop."""
    return await anyio.to_thread.run_sync(partial(func, *args, **kwargs))
