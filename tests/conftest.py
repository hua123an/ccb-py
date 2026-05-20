from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from _pytest.fixtures import FixtureDef, resolve_fixture_function


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "asyncio: mark test to run in an asyncio event loop")


@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


def _get_loop(request: pytest.FixtureRequest) -> asyncio.AbstractEventLoop:
    return request.getfixturevalue("event_loop")


def _run_awaitable(loop: asyncio.AbstractEventLoop, awaitable: Awaitable[Any]) -> Any:
    previous_loop: asyncio.AbstractEventLoop | None
    try:
        previous_loop = asyncio.get_event_loop()
    except RuntimeError:
        previous_loop = None

    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(awaitable)
    finally:
        asyncio.set_event_loop(previous_loop)


def _fixture_kwargs(fixturedef: FixtureDef[Any], request: pytest.FixtureRequest) -> dict[str, Any]:
    return {argname: request.getfixturevalue(argname) for argname in fixturedef.argnames}


def _test_kwargs(pyfuncitem: pytest.Function) -> dict[str, Any]:
    signature = inspect.signature(pyfuncitem.obj)
    return {
        name: value
        for name, value in pyfuncitem.funcargs.items()
        if name in signature.parameters
    }


def _finish_asyncgen_fixture(
    loop: asyncio.AbstractEventLoop,
    fixturefunc: Callable[..., Any],
    agen: Any,
) -> None:
    try:
        _run_awaitable(loop, agen.__anext__())
    except StopAsyncIteration:
        return

    pytest.fail(
        "fixture function has more than one 'yield':\n\n"
        f"{inspect.getsource(fixturefunc)}",
        pytrace=False,
    )


@pytest.hookimpl(tryfirst=True)
def pytest_fixture_setup(
    fixturedef: FixtureDef[Any], request: pytest.FixtureRequest
) -> Any:
    fixturefunc = resolve_fixture_function(fixturedef, request)
    if not (
        inspect.iscoroutinefunction(fixturefunc)
        or inspect.isasyncgenfunction(fixturefunc)
    ):
        return None

    loop = _get_loop(request)
    kwargs = _fixture_kwargs(fixturedef, request)
    cache_key = fixturedef.cache_key(request)

    try:
        if inspect.isasyncgenfunction(fixturefunc):
            agen = fixturefunc(**kwargs)
            result = _run_awaitable(loop, agen.__anext__())
            request.addfinalizer(
                functools.partial(_finish_asyncgen_fixture, loop, fixturefunc, agen)
            )
        else:
            result = _run_awaitable(loop, fixturefunc(**kwargs))
    except BaseException as exc:
        fixturedef.cached_result = (None, cache_key, (exc, exc.__traceback__))
        raise

    fixturedef.cached_result = (result, cache_key, None)
    return result


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    test_fn = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_fn):
        return None

    loop = pyfuncitem._request.getfixturevalue("event_loop")
    _run_awaitable(loop, test_fn(**_test_kwargs(pyfuncitem)))
    return True
