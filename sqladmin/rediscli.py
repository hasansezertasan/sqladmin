"""
Base structure is from the Flask-Admin project.
"""
import shlex
from typing import TYPE_CHECKING, Any, Callable, Dict, Tuple, Union

from starlette.requests import Request
from starlette.responses import Response

from sqladmin.application import expose
from sqladmin.models import BaseView

if TYPE_CHECKING:
    from redis import Redis


class TextWrapper(str):
    pass


class RedisCLI(BaseView):
    """Base class for Redis CLI."""

    remapped_commands = {"del": "delete"}
    """List of redis remapped commands."""

    excluded_commands = set(("pubsub", "set_response_callback", "from_url"))
    """List of excluded commands."""

    def __init__(self, redis: Redis) -> None:
        """
        Args:
            redis: Redis connection object.
        """
        self.redis = redis
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
            if not callable(attr) and name in self.remapped_commands:
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
    ) -> Union[tuple | list | bool | str | bytes | TextWrapper | dict]:
        """
        Execute single command.

        Args:
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

    async def before_execution(self, request: Request, parts: Tuple[str, ...]) -> None:
        """Perform some actions before the command execution.
        By default, does nothing.

        Args:
            request: Incoming request.
            parts: Command parts.
        """

    async def after_execution(
        self,
        request: Request,
        parts: Tuple[str, ...],
        result: Union[tuple | list | bool | str | bytes | TextWrapper | dict],
    ) -> None:
        """Perform some actions after the command execution.
        By default, does nothing.

        Args:
            request: Incoming request.
            parts: Command parts.
            result: Command result.
        """

    @expose("/", methods=["GET", "POST"], identity="rediscli")
    async def index(self, request: Request) -> Response:
        """Render the Redis CLI."""
        if request.method == "GET":
            return await self.templates.TemplateResponse(
                request, "sqladmin/rediscli/index.html"
            )
        try:
            form = await request.form()
            cmd: Any = form.get("cmd")
            if not cmd:
                return await self._error(request, "CLI: Empty command.")

            parts = self._parse_cmd(cmd)
            if not parts:
                return await self._error(request, "CLI: Failed to parse command.")

            await self.before_execution(request, parts)
            result = await self._execute_command(request, parts[0], parts[1:])
            await self.after_execution(request, parts, result)
            return await self.templates.TemplateResponse(
                request,
                "admin/rediscli/response.html",
                context={"type_name": lambda d: type(d).__name__, "result": result},
            )
        except Exception as e:
            msg = f"CLI: {e}"
            return await self._error(request, msg)
