from collections.abc import AsyncGenerator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional, Union, cast

from litestar.cli._utils import console  # pyright: ignore
from litestar.constants import HTTP_RESPONSE_START
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from advanced_alchemy.base import metadata_registry
from advanced_alchemy.config.asyncio import SQLAlchemyAsyncConfig as _SQLAlchemyAsyncConfig
from advanced_alchemy.extensions.litestar._utils import (
    delete_aa_scope_state,
    get_aa_scope_state,
    set_aa_scope_state,
)
from advanced_alchemy.extensions.litestar.plugins.init.config.common import (
    SESSION_SCOPE_KEY,
    SESSION_TERMINUS_ASGI_EVENTS,
)
from advanced_alchemy.extensions.litestar.plugins.init.config.engine import EngineConfig

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Coroutine

    from litestar import Litestar
    from litestar.datastructures.state import State
    from litestar.types import BeforeMessageSendHookHandler, Message, Scope

    # noinspection PyUnresolvedReferences


__all__ = (
    "SQLAlchemyAsyncConfig",
    "autocommit_before_send_handler",
    "autocommit_handler_maker",
    "default_before_send_handler",
    "default_handler_maker",
)


def default_handler_maker(
    session_scope_key: str = SESSION_SCOPE_KEY,
) -> "Callable[[Message, Scope], Coroutine[Any, Any, None]]":
    """Set up the handler to issue a transaction commit or rollback based on specified status codes
    Args:
        session_scope_key: The key to use within the application state

    Returns:
        The handler callable
    """

    async def handler(message: "Message", scope: "Scope") -> None:
        """Handle commit/rollback, closing and cleaning up sessions before sending.

        Args:
            message: ASGI-``Message``
            scope: An ASGI-``Scope``
        """
        session = cast("Optional[AsyncSession]", get_aa_scope_state(scope, session_scope_key))
        if session and message["type"] in SESSION_TERMINUS_ASGI_EVENTS:
            await session.close()
            delete_aa_scope_state(scope, session_scope_key)

    return handler


default_before_send_handler = default_handler_maker()


def autocommit_handler_maker(
    commit_on_redirect: bool = False,
    extra_commit_statuses: Optional[set[int]] = None,
    extra_rollback_statuses: Optional[set[int]] = None,
    session_scope_key: str = SESSION_SCOPE_KEY,
) -> "Callable[[Message, Scope], Coroutine[Any, Any, None]]":
    """Set up the handler to issue a transaction commit or rollback based on specified status codes
    Args:
        commit_on_redirect: Issue a commit when the response status is a redirect (``3XX``)
        extra_commit_statuses: A set of additional status codes that trigger a commit
        extra_rollback_statuses: A set of additional status codes that trigger a rollback
        session_scope_key: The key to use within the application state

    Raises:
        ValueError: If the extra commit statuses and extra rollback statuses share any status codes

    Returns:
        The handler callable
    """
    if extra_commit_statuses is None:
        extra_commit_statuses = set()

    if extra_rollback_statuses is None:
        extra_rollback_statuses = set()

    if len(extra_commit_statuses & extra_rollback_statuses) > 0:
        msg = "Extra rollback statuses and commit statuses must not share any status codes"
        raise ValueError(msg)

    commit_range = range(200, 400 if commit_on_redirect else 300)

    async def handler(message: "Message", scope: "Scope") -> None:
        """Handle commit/rollback, closing and cleaning up sessions before sending.

        Args:
            message: ASGI-``litestar.types.Message``
            scope: An ASGI-``litestar.types.Scope``
        """
        session = cast("Optional[AsyncSession]", get_aa_scope_state(scope, session_scope_key))
        try:
            if session is not None and message["type"] == HTTP_RESPONSE_START:
                if (message["status"] in commit_range or message["status"] in extra_commit_statuses) and message[
                    "status"
                ] not in extra_rollback_statuses:
                    await session.commit()
                else:
                    await session.rollback()
        finally:
            if session and message["type"] in SESSION_TERMINUS_ASGI_EVENTS:
                await session.close()
                delete_aa_scope_state(scope, session_scope_key)

    return handler


autocommit_before_send_handler = autocommit_handler_maker()


@dataclass
class SQLAlchemyAsyncConfig(_SQLAlchemyAsyncConfig):
    """Litestar Async SQLAlchemy Configuration."""

    before_send_handler: Optional[
        Union["BeforeMessageSendHookHandler", Literal["autocommit", "autocommit_include_redirects"]]
    ] = None
    """Handler to call before the ASGI message is sent.

    The handler should handle closing the session stored in the ASGI scope, if it's still open, and committing and
    uncommitted data.
    """
    engine_dependency_key: str = "db_engine"
    """Key to use for the dependency injection of database engines."""
    session_dependency_key: str = "db_session"
    """Key to use for the dependency injection of database sessions."""
    engine_app_state_key: str = "db_engine"
    """Key under which to store the SQLAlchemy engine in the application :class:`State <litestar.datastructures.State>`
    instance.
    """
    session_maker_app_state_key: str = "session_maker_class"
    """Key under which to store the SQLAlchemy :class:`sessionmaker <sqlalchemy.orm.sessionmaker>` in the application
    :class:`State <litestar.datastructures.State>` instance.
    """
    session_scope_key: str = SESSION_SCOPE_KEY
    """Key under which to store the SQLAlchemy scope in the application."""
    engine_config: EngineConfig = field(default_factory=EngineConfig)  # pyright: ignore[reportIncompatibleVariableOverride]
    """Configuration for the SQLAlchemy engine.

    The configuration options are documented in the SQLAlchemy documentation.
    """
    set_default_exception_handler: bool = True
    """Sets the default exception handler on application start."""

    def _ensure_unique(self, registry_name: str, key: str, new_key: Optional[str] = None, _iter: int = 0) -> str:
        new_key = new_key if new_key is not None else key
        if new_key in getattr(self.__class__, registry_name, {}):
            _iter += 1
            new_key = self._ensure_unique(registry_name, key, f"{key}_{_iter}", _iter)
        return new_key

    def __post_init__(self) -> None:
        self.session_scope_key = self._ensure_unique("_SESSION_SCOPE_KEY_REGISTRY", self.session_scope_key)
        self.engine_app_state_key = self._ensure_unique("_ENGINE_APP_STATE_KEY_REGISTRY", self.engine_app_state_key)
        self.session_maker_app_state_key = self._ensure_unique(
            "_SESSIONMAKER_APP_STATE_KEY_REGISTRY",
            self.session_maker_app_state_key,
        )
        self.__class__._SESSION_SCOPE_KEY_REGISTRY.add(self.session_scope_key)  # noqa: SLF001
        self.__class__._ENGINE_APP_STATE_KEY_REGISTRY.add(self.engine_app_state_key)  # noqa: SLF001
        self.__class__._SESSIONMAKER_APP_STATE_KEY_REGISTRY.add(self.session_maker_app_state_key)  # noqa: SLF001
        if self.before_send_handler is None:
            self.before_send_handler = default_handler_maker(session_scope_key=self.session_scope_key)
        if self.before_send_handler == "autocommit":
            self.before_send_handler = autocommit_handler_maker(session_scope_key=self.session_scope_key)
        if self.before_send_handler == "autocommit_include_redirects":
            self.before_send_handler = autocommit_handler_maker(
                session_scope_key=self.session_scope_key,
                commit_on_redirect=True,
            )
        super().__post_init__()

    def create_session_maker(self) -> "Callable[[], AsyncSession]":
        """Get a session maker. If none exists yet, create one.

        Returns:
            Session factory used by the plugin.
        """
        if self.session_maker:
            return self.session_maker

        session_kws = self.session_config_dict
        if session_kws.get("bind") is None:
            session_kws["bind"] = self.get_engine()
        return self.session_maker_class(**session_kws)  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]

    @asynccontextmanager
    async def lifespan(
        self,
        app: "Litestar",
    ) -> "AsyncGenerator[None, None]":
        deps = self.create_app_state_items()
        app.state.update(deps)
        try:
            if self.create_all:
                await self.create_all_metadata(app)
            yield
        finally:
            if self.engine_dependency_key in deps:
                engine = deps[self.engine_dependency_key]
                if hasattr(engine, "dispose"):
                    await cast("AsyncEngine", engine).dispose()

    def provide_engine(self, state: "State") -> "AsyncEngine":
        """Create an engine instance.

        Args:
            state: The ``Litestar.state`` instance.

        Returns:
            An engine instance.
        """
        return cast("AsyncEngine", state.get(self.engine_app_state_key))

    def provide_session(self, state: "State", scope: "Scope") -> "AsyncSession":
        """Create a session instance.

        Args:
            state: The ``Litestar.state`` instance.
            scope: The current connection's scope.

        Returns:
            A session instance.
        """
        # Import locally to avoid potential circular dependency issues at module level
        from advanced_alchemy._listeners import set_async_context

        session = cast("Optional[AsyncSession]", get_aa_scope_state(scope, self.session_scope_key))
        if session is None:
            session_maker = cast("Callable[[], AsyncSession]", state[self.session_maker_app_state_key])
            session = session_maker()
            set_aa_scope_state(scope, self.session_scope_key, session)

        set_async_context(True)  # Set context before yielding
        return session

    @property
    def signature_namespace(self) -> dict[str, Any]:
        """Return the plugin's signature namespace.

        Returns:
            A string keyed dict of names to be added to the namespace for signature forward reference resolution.
        """
        return {"AsyncEngine": AsyncEngine, "AsyncSession": AsyncSession}

    async def create_all_metadata(self, app: "Litestar") -> None:
        """Create all metadata

        Args:
            app (Litestar): The ``Litestar`` instance
        """
        async with self.get_engine().begin() as conn:
            try:
                await conn.run_sync(metadata_registry.get(self.bind_key).create_all)
            except OperationalError as exc:
                console.print(f"[bold red] * Could not create target metadata.  Reason: {exc}")

    def create_app_state_items(self) -> dict[str, Any]:
        """Key/value pairs to be stored in application state.

        Returns:
            A dictionary of key/value pairs to be stored in application state.
        """
        return {
            self.engine_app_state_key: self.get_engine(),
            self.session_maker_app_state_key: self.create_session_maker(),
        }

    def update_app_state(self, app: "Litestar") -> None:
        """Set the app state with engine and session.

        Args:
            app: The ``Litestar`` instance.
        """
        app.state.update(self.create_app_state_items())
