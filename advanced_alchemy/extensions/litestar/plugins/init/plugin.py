import contextlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Union, cast

from litestar.di import Provide
from litestar.dto import DTOData
from litestar.params import Dependency, Parameter
from litestar.plugins import CLIPlugin, InitPluginProtocol
from sqlalchemy.ext.asyncio import AsyncSession, async_scoped_session
from sqlalchemy.orm import Session, scoped_session

from advanced_alchemy.exceptions import ImproperConfigurationError, RepositoryError
from advanced_alchemy.extensions.litestar.exception_handler import exception_to_http_response
from advanced_alchemy.extensions.litestar.plugins import _slots_base
from advanced_alchemy.filters import (
    BeforeAfter,
    CollectionFilter,
    ComparisonFilter,
    ExistsFilter,
    FilterGroup,
    FilterMap,
    FilterTypes,
    InAnyFilter,
    LimitOffset,
    LogicalOperatorMap,
    MultiFilter,
    NotExistsFilter,
    NotInCollectionFilter,
    NotInSearchFilter,
    OnBeforeAfter,
    OrderBy,
    SearchFilter,
    StatementFilter,
    StatementTypeT,
)
from advanced_alchemy.service import ModelDictListT, ModelDictT, ModelDTOT, ModelOrRowMappingT, ModelT, OffsetPagination

if TYPE_CHECKING:
    from click import Group
    from litestar.config.app import AppConfig
    from litestar.types import BeforeMessageSendHookHandler

    from advanced_alchemy.extensions.litestar.plugins.init.config import SQLAlchemyAsyncConfig, SQLAlchemySyncConfig

__all__ = ("SQLAlchemyInitPlugin",)

signature_namespace_values: dict[str, Any] = {
    "BeforeAfter": BeforeAfter,
    "OnBeforeAfter": OnBeforeAfter,
    "CollectionFilter": CollectionFilter,
    "LimitOffset": LimitOffset,
    "OrderBy": OrderBy,
    "SearchFilter": SearchFilter,
    "NotInCollectionFilter": NotInCollectionFilter,
    "NotInSearchFilter": NotInSearchFilter,
    "FilterTypes": FilterTypes,
    "OffsetPagination": OffsetPagination,
    "ExistsFilter": ExistsFilter,
    "Parameter": Parameter,
    "Dependency": Dependency,
    "DTOData": DTOData,
    "Sequence": Sequence,
    "ModelT": ModelT,
    "ModelDictT": ModelDictT,
    "ModelDTOT": ModelDTOT,
    "ModelDictListT": ModelDictListT,
    "ModelOrRowMappingT": ModelOrRowMappingT,
    "Session": Session,
    "scoped_session": scoped_session,
    "AsyncSession": AsyncSession,
    "async_scoped_session": async_scoped_session,
    "FilterGroup": FilterGroup,
    "NotExistsFilter": NotExistsFilter,
    "MultiFilter": MultiFilter,
    "ComparisonFilter": ComparisonFilter,
    "StatementTypeT": StatementTypeT,
    "StatementFilter": StatementFilter,
    "LogicalOperatorMap": LogicalOperatorMap,
    "InAnyFilter": InAnyFilter,
    "FilterMap": FilterMap,
}


class SQLAlchemyInitPlugin(InitPluginProtocol, CLIPlugin, _slots_base.SlotsBase):
    """SQLAlchemy application lifecycle configuration."""

    def __init__(
        self,
        config: Union[
            "SQLAlchemyAsyncConfig",
            "SQLAlchemySyncConfig",
            "Sequence[Union[SQLAlchemyAsyncConfig, SQLAlchemySyncConfig]]",
        ],
    ) -> None:
        """Initialize ``SQLAlchemyPlugin``.

        Args:
            config: configure DB connection and hook handlers and dependencies.
        """
        self._config = config

    @property
    def config(self) -> "Sequence[Union[SQLAlchemyAsyncConfig, SQLAlchemySyncConfig]]":
        return self._config if isinstance(self._config, Sequence) else [self._config]

    def on_cli_init(self, cli: "Group") -> None:
        from advanced_alchemy.extensions.litestar.cli import database_group

        cli.add_command(database_group)

    def _validate_config(self) -> None:
        configs = self._config if isinstance(self._config, Sequence) else [self._config]
        scope_keys = {config.session_scope_key for config in configs}
        engine_keys = {config.engine_dependency_key for config in configs}
        session_keys = {config.session_dependency_key for config in configs}
        if len(configs) > 1 and any(len(i) != len(configs) for i in (scope_keys, engine_keys, session_keys)):
            raise ImproperConfigurationError(
                detail="When using multiple configurations, please ensure the `session_dependency_key` and `engine_dependency_key` settings are unique across all configs.  Additionally, iF you are using a custom `before_send` handler, ensure `session_scope_key` is unique.",
            )

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        """Configure application for use with SQLAlchemy.

        Args:
            app_config: The :class:`AppConfig <.config.app.AppConfig>` instance.
        """
        self._validate_config()
        with contextlib.suppress(ImportError):
            from asyncpg.pgproto import pgproto  # pyright: ignore[reportMissingImports]

            signature_namespace_values.update({"pgproto.UUID": pgproto.UUID})
            app_config.type_encoders = {pgproto.UUID: str, **(app_config.type_encoders or {})}
        with contextlib.suppress(ImportError):
            import uuid_utils  # pyright: ignore[reportMissingImports]

            signature_namespace_values.update({"uuid_utils.UUID": uuid_utils.UUID})  # pyright: ignore[reportUnknownMemberType]
            app_config.type_encoders = {uuid_utils.UUID: str, **(app_config.type_encoders or {})}  # pyright: ignore[reportUnknownMemberType]
            app_config.type_decoders = [
                (lambda x: x is uuid_utils.UUID, lambda t, v: t(str(v))),  # pyright: ignore[reportUnknownMemberType]
                *(app_config.type_decoders or []),
            ]
        configure_exception_handler = False
        for config in self.config:
            if config.set_default_exception_handler:
                configure_exception_handler = True
            signature_namespace_values.update(config.signature_namespace)
            app_config.lifespan.append(config.lifespan)  # pyright: ignore[reportUnknownMemberType]

            app_config.dependencies.update(
                {
                    config.engine_dependency_key: Provide(config.provide_engine, sync_to_thread=False),
                    config.session_dependency_key: Provide(config.provide_session, sync_to_thread=False),
                },
            )
            app_config.before_send.append(cast("BeforeMessageSendHookHandler", config.before_send_handler))
        app_config.signature_namespace.update(signature_namespace_values)
        if configure_exception_handler and not any(
            isinstance(exc, int) or issubclass(exc, RepositoryError)
            for exc in app_config.exception_handlers  # pyright: ignore[reportUnknownMemberType]
        ):
            app_config.exception_handlers.update({RepositoryError: exception_to_http_response})  # pyright: ignore[reportUnknownMemberType]

        return app_config
