"""
Base structure is from the Flask-Admin project.
"""

import shlex
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Dict, List, Tuple, Union

from starlette.requests import Request
from starlette.responses import Response

from sqladmin.application import expose
from sqladmin.models import BaseView

if TYPE_CHECKING:
    from redis import Redis


class TextWrapper(str):
    pass


class RedisCLIView(BaseView):
    """Base class for Redis CLI.

    ???+ usage
        ```python
        from redis import Redis
        from starlette.applications import Starlette

        from sqladmin import Admin
        from sqladmin.rediscli import RedisCLIView

        redis = Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            username=REDIS_USERNAME,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )


        class RedisCLI(RedisCLIView):
            redis = redis


        app = Starlette()
        admin = Admin(app)
        admin.add_view(RedisCLI)
        # uvicorn main:app --reload
        ```
    """

    name: ClassVar[str] = "Redis CLI"
    """Name of the view to be displayed."""

    identity: ClassVar[str] = "rediscli"
    """Same as name but it will be used for URL of the endpoints."""

    methods: ClassVar[List[str]] = ["GET", "POST"]
    """List of method names for the endpoint.
    By default it's set to `["GET", "POST"]` only.
    """

    remapped_commands = {"del": "delete"}
    """List of redis remapped commands."""

    excluded_commands = set(("pubsub", "set_response_callback", "from_url"))
    """List of excluded commands."""

    redis: "Redis"

    def __init__(self) -> None:
        if not self.redis:
            raise ValueError("Redis connection is not provided.")
        self.commands: Dict[str, Tuple[Callable[..., Any], str]] = {}

        self._inspect_commands()
        self._contribute_commands()

    def _inspect_commands(self) -> None:
        """
        Inspect connection object and extract command names.
        """
        for name in dir(self.redis):
            if name.startswith("_"):
                continue
            attr = getattr(self.redis, name)
            if not callable(attr) and name in self.excluded_commands:
                continue
            doc = (getattr(attr, "__doc__", "") or "").strip()
            self.commands[name] = (attr, doc)

        for new, old in self.remapped_commands.items():
            self.commands[new] = self.commands[old]

    def _contribute_commands(self) -> None:
        """Contribute custom commands."""
        self.commands["help"] = (self._cmd_help, "Help!")

    def _cmd_help(self, *args: Any) -> TextWrapper:
        """
        Help command implementation.
        """
        if not args:
            help = "Usage: help <command>.\nList of supported commands: "
            help += ", ".join(n for n in sorted(self.commands))
            return TextWrapper(help)

        cmd: str = args[0]
        if cmd not in self.commands:
            raise KeyError("Invalid command.")

        help = self.commands[cmd][1]
        if not help:
            return TextWrapper("Command does not have any help.")

        return TextWrapper(help)

    def _parse_cmd(self, cmd: str) -> tuple:
        """
        Parse command by using shlex module.

        Args:
            cmd: Command to parse.
        """
        return tuple(shlex.split(cmd))

    async def _execute_command(
        self, request: Request, name: str, args: Tuple[str, ...]
    ) -> Union[tuple, list, bool, str, bytes, TextWrapper, dict]:
        """
        Execute single command.

        Args:
            request: Incoming request.
            name: Command name.
            args: Command arguments.
        """
        new_cmd = self.remapped_commands.get(name)
        if new_cmd:
            name = new_cmd

        if name not in self.commands:
            raise KeyError("Invalid command.")

        handler, _ = self.commands[name]
        return handler(*args)

    async def _error(self, request: Request, msg: str) -> Response:
        """
        Format error message as HTTP response.

        Args:
            msg: Message to format.
        """
        return await self.templates.TemplateResponse(
            request,
            "admin/rediscli/error.html",
            context={"error": msg},
        )

    async def before_execution(
        self,
        request: Request,
        name: str,
        args: Tuple[str, ...],
    ) -> None:
        """Perform some actions before the command execution.
        By default, does nothing.

        Args:
            request: Incoming request.
            name: Command name.
            args: Command arguments.
        """

    async def after_execution(
        self,
        request: Request,
        name: str,
        args: Tuple[str, ...],
        result: Union[tuple, list, bool, str, bytes, TextWrapper, dict],
    ) -> None:
        """Perform some actions after the command execution.
        By default, does nothing.

        Args:
            request: Incoming request.
            name: Command name.
            args: Command arguments.
            result: Command result.
        """

    async def can_use_command(
        self,
        request: Request,
        name: str,
        args: Tuple[str, ...],
    ) -> bool:
        """Check if user can use the command.
        By default, always returns True.

        Args:
            request: Incoming request.
            name: Command name.
            args: Command arguments.
        """
        return True

    @expose("/rediscli", methods=["GET", "POST"], identity="rediscli")
    async def index_page(self, request: Request) -> Response:
        """Render the Redis CLI."""
        if request.method == "GET":
            return await self.templates.TemplateResponse(
                request, "sqladmin/rediscli/index.html", context={"request": request}
            )
        try:
            form = await request.form()
            cmd: Any = form.get("cmd")
            if not cmd:
                return await self._error(request, "CLI: Empty command.")

            parts = self._parse_cmd(cmd)
            if not parts:
                return await self._error(request, "CLI: Failed to parse command.")

            cmd_name = parts[0]
            cmd_args = parts[1:]
            await self.before_execution(request, cmd_name, cmd_args)
            if not await self.can_use_command(request, cmd_name, cmd_args):
                return await self._error(request, "CLI: Command is not allowed.")
            result = await self._execute_command(request, cmd_name, cmd_args)
            await self.after_execution(request, cmd_name, cmd_args, result)
            return await self.templates.TemplateResponse(
                request,
                "admin/rediscli/response.html",
                context={"type_name": lambda d: type(d).__name__, "result": result},
            )
        except Exception as e:
            msg = f"CLI: {e}"
            return await self._error(request, msg)
