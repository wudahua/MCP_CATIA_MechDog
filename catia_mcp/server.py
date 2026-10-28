"""MCP transport with one shared CATIA connection and one serial STA worker."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from jsonschema import ValidationError, validate
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from catia_mcp import __version__
from catia_mcp.connection import CATIAConnection
from catia_mcp.results import NO_RECOVERY, failure_code, is_read_only, operation_result
from catia_mcp.tools.assembly import AssemblyTools
from catia_mcp.tools.diagnostics import DiagnosticsTools
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.planner import PlannerTools
from catia_mcp.tools.sketcher import SketcherTools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "catia_mcp.log"),
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("catia_mcp")


def structured_result(data: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text", text=json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2)
            )
        ],
        structuredContent=data,
        isError=data.get("ok") is not True,
    )


def failure(
    code: str,
    message: str,
    stage: str,
    modified: bool | None,
    tool: str,
    rolled_back: bool | None = None,
    recovery: Any = None,
    next_actions: list[str] | None = None,
) -> CallToolResult:
    return structured_result(
        operation_result(
            tool=tool,
            ok=False,
            code=code,
            message=message,
            data={"failure_stage": stage},
            modified=modified,
            rolled_back=rolled_back,
            recovery=recovery,
            next_actions=next_actions,
        )
    )


class CATIAMCPServer:
    def __init__(self) -> None:
        # Pass the project version explicitly. Without it the mcp library fills
        # in ITS OWN version, so a client doing initialize saw
        # serverInfo.version = "1.30.0" - the version of the dependency - for a
        # 0.3.2 server. Reported by a user installing v0.3.2 on V5-6R2022.
        self.server = Server("catia-v5-mcp", version=__version__)
        self.connection = CATIAConnection()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="catia-sta")
        self.document_tools = DocumentTools(self.connection)
        self.sketcher_tools = SketcherTools(self.connection)
        self.part_design_tools = PartDesignTools(self.connection)
        self.gsd_tools = GSDTools(self.connection)
        self.diagnostics_tools = DiagnosticsTools(self.connection)
        self.assembly_tools = AssemblyTools(self.connection)
        self.measurement_tools = MeasurementTools(self.connection)
        self.export_tools = ExportTools(self.connection)
        self.planner_tools = PlannerTools(self.connection)
        self._tool_modules = [
            self.document_tools,
            self.sketcher_tools,
            self.part_design_tools,
            self.gsd_tools,
            self.diagnostics_tools,
            self.assembly_tools,
            self.measurement_tools,
            self.export_tools,
            self.planner_tools,
        ]
        self._tool_router: dict[str, Any] = {}
        self._schemas: dict[str, dict] = {}
        for module in self._tool_modules:
            for definition in module.get_tool_definitions():
                name = definition["name"]
                if name in self._tool_router:
                    raise ValueError(f"Duplicate tool: {name}")
                self._tool_router[name] = module
                self._schemas[name] = definition["inputSchema"]
        # The planner dispatches sub-steps through this same router, so it can
        # only be bound once every module has registered its tools.
        self.planner_tools.bind(self._tool_router)
        # Let catia_diagnose report the count it actually registered.
        self.diagnostics_tools.tool_count = len(self._tool_router)
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        @self.server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            return [
                Tool(**definition)
                for module in self._tool_modules
                for definition in module.get_tool_definitions()
            ]

        @self.server.call_tool(validate_input=False)
        async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> CallToolResult:
            return await self.call_tool(name, arguments or {})

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        future = asyncio.get_running_loop().run_in_executor(
            self._executor, self._execute_tool, name, arguments
        )
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            future.add_done_callback(
                lambda completed: completed.exception() if not completed.cancelled() else None
            )
            raise

    def _session_state(self) -> str:
        """A short snapshot of the document state, for failure messages.

        CATIA reports automation failures as a bare "method failed" - the
        actual cause is almost always the document state, not the arguments.
        Three of the bugs found on 2026-10-22 were invisible for exactly this
        reason: the in-work object sitting on a geometrical set, a topology
        reference that had been invalidated by clearing the selection, and a
        body whose last feature had not been rebuilt yet. None of them produced
        a message that pointed at the state.

        The readings live in ``connection.session_snapshot`` so that
        ``catia_diagnose`` publishes the SAME state this line quotes - the
        message used to say "catia_diagnose reports both" while diagnose
        reported neither (reported 2026-10-27).
        """
        from catia_mcp.connection import session_line

        try:
            return session_line(self.connection)
        except Exception:  # noqa: BLE001 - a message must never fail here
            return "document=<unknown>"

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        module = self._tool_router.get(name)
        if module is None:
            return failure("UNKNOWN_TOOL", f"Unknown tool: {name}", "validate", False, name)
        try:
            validate(arguments, self._schemas[name])
        except ValidationError as error:
            return failure("INVALID_ARGUMENT", error.message, "validate", False, name)
        logger.info("Tool call: %s(%s)", name, arguments)
        stage = "connect"
        try:
            if name not in {"catia_connect", "catia_disconnect"}:
                self.connection.ensure_connected()
            stage = "connect" if name == "catia_connect" else "execute"
            result = module.execute(name, arguments)
            if isinstance(result, dict):
                logger.info("Tool result: %s %s", name, result.get("code"))
                return structured_result(result)
            logger.info("Tool result: %s", result[:200])
            return CallToolResult(content=[TextContent(type="text", text=result)])
        except Exception as error:
            logger.exception("Tool failed: %s: %s", name, error)  # noqa: TRY401 -- log preview includes the error message
            # The code comes from the failure, not from its Python type. This line
            # used to be `"UNSUPPORTED_CAPABILITY" if isinstance(AttributeError)
            # else "COM_ERROR"`, which flattened NO_MATERIAL and SKETCH_NOT_FOUND
            # into COM_ERROR on the bare-call path while catia_run_plan kept them
            # - one failure, two semantics, reported 2026-10-28 as a P0.
            code = failure_code(str(error), error, default="COM_ERROR")
            message = str(error)
            if code == "UNSUPPORTED_CAPABILITY":
                message = (
                    f"UNSUPPORTED_CAPABILITY in {name}: the required CATIA automation API "
                    f"did not resolve ({error}). The API may be absent from this CATIA release, "
                    "or a stale pywin32 gen_py cache may be blocking resolution. Clearing caches "
                    "cannot add an absent API. For a stale cache, close CATIA and the server, "
                    "delete the %TEMP%\\gen_py folder, restart and retry once. A missing "
                    "workbench licence can also lock methods. Run catia_diagnose for details."
                )
            elif stage == "execute":
                message = (
                    f"{error}\n\n"
                    f"Session state: {self._session_state()}\n"
                    "CATIA reports most automation failures as a bare 'method failed'. Before "
                    "reading that as a bad sketch, bad edge or bad argument, check the state "
                    "above: a Part Design factory called while in_work_object is a geometrical "
                    "set fails exactly this way, and so does a topology reference whose "
                    "selection has been cleared. catia_diagnose reports the same state in its "
                    "'session' block, with the reasons next to the readings that could not be "
                    "taken."
                )
            # A tool that cannot write has no model to repair, so the recovery block
            # (and its "inspect the model" advice) is left out instead of filled
            # with a sentence that is wrong. Modified is a known no, not unknown.
            # A tool that DOES write can still know it wrote nothing - a refusal
            # that happened at the name lookup carries `modified=False` with it.
            read_only = is_read_only(name)
            modified = False if (read_only or stage == "connect") else \
                getattr(error, "modified", None)
            return failure(code, message, stage, modified, name,
                           rolled_back=getattr(error, "rolled_back", None),
                           recovery=NO_RECOVERY if read_only else None,
                           next_actions=getattr(error, "next_actions", None))

    async def close(self) -> None:
        try:
            await asyncio.get_running_loop().run_in_executor(
                self._executor, self.connection.disconnect
            )
        finally:
            self._executor.shutdown(wait=True)

    async def run(self) -> None:
        logger.info("Starting CATIA V5 MCP Server: %d tools", len(self._tool_router))
        try:
            async with stdio_server() as (read_stream, write_stream):
                await self.server.run(
                    read_stream, write_stream, self.server.create_initialization_options()
                )
        finally:
            await self.close()


    async def run_http(self, host: str, port: int, token: str | None) -> None:
        from catia_mcp.http_transport import serve

        logger.info("Starting CATIA V5 MCP Server (HTTP): %d tools", len(self._tool_router))
        try:
            await serve(self.server, host, port, token)
        finally:
            await self.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    from catia_mcp.http_transport import DEFAULT_HOST, DEFAULT_PORT

    parser = argparse.ArgumentParser(
        prog="catia-mcp",
        description="CATIA V5 MCP server. Uses stdio by default; --http serves remote agents.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve over Streamable HTTP at /mcp instead of stdio (remote agents, Mac clients)",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port (default 8765)")
    parser.add_argument(
        "--token",
        default=os.environ.get("CATIA_MCP_TOKEN"),
        help="Bearer token required from clients (default: CATIA_MCP_TOKEN env var). "
        "Mandatory when --host is not a loopback address.",
    )
    parser.add_argument(
        "--new-token", action="store_true", help="Print a fresh random token and exit"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.new_token:
        from catia_mcp.http_transport import new_token

        print(new_token())
        return
    if args.http:
        from catia_mcp.http_transport import check_bind

        check_bind(args.host, args.token)
        asyncio.run(CATIAMCPServer().run_http(args.host, args.port, args.token))
        return
    asyncio.run(CATIAMCPServer().run())


if __name__ == "__main__":
    main()
