"""Expose Microsoft WorkIQ's stdio MCP server as an LLM toolbox."""

import atexit
import base64
import json
import math
import queue
import shutil
import subprocess
import threading
import time

import llm


class WorkIQError(Exception):
    """WorkIQ could not complete an MCP request."""


class _StdioClient:
    def __init__(self, command, timeout):
        self.command = command
        self.timeout = timeout
        self._process = None
        self._responses = None
        self._next_id = 0
        self._lock = threading.RLock()
        atexit.register(self.close)

    def _start(self):
        executable = shutil.which(self.command[0])
        if executable is None:
            raise WorkIQError(
                f"Executable not found: {self.command[0]}. "
                "Install the official WorkIQ CLI and run `workiq auth login` first."
            )
        try:
            self._process = subprocess.Popen(
                [executable, *self.command[1:], "mcp"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as ex:
            raise WorkIQError(f"Could not start WorkIQ: {ex}") from ex
        self._responses = queue.Queue()
        threading.Thread(
            target=self._read_stdout,
            args=(self._process.stdout, self._responses),
            daemon=True,
        ).start()
        self._exchange(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "llm-workiq", "version": "0.1.0"},
            },
        )
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    @staticmethod
    def _read_stdout(stream, responses):
        try:
            for line in stream:
                responses.put(line)
        finally:
            responses.put(None)

    def _write(self, message):
        self._process.stdin.write(json.dumps(message) + "\n")
        self._process.stdin.flush()

    def _exchange(self, method, params):
        self._next_id += 1
        request_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkIQError(f"WorkIQ timed out after {self.timeout:g}s during {method}.")
            try:
                line = self._responses.get(timeout=remaining)
            except queue.Empty as ex:
                raise WorkIQError(
                    f"WorkIQ timed out after {self.timeout:g}s during {method}."
                ) from ex
            if line is None:
                raise WorkIQError(
                    "WorkIQ closed its MCP connection. "
                    "Check `workiq auth status`; sign in with `workiq auth login` if needed."
                )
            try:
                message = json.loads(line)
            except ValueError as ex:
                raise WorkIQError("WorkIQ returned invalid JSON on its MCP stdout.") from ex
            if not isinstance(message, dict):
                raise WorkIQError("WorkIQ returned an invalid MCP message.")
            if "method" in message:
                if "id" in message:
                    reply = {"jsonrpc": "2.0", "id": message["id"]}
                    if message["method"] == "ping":
                        reply["result"] = {}
                    else:
                        reply["error"] = {"code": -32601, "message": "Method not supported"}
                    self._write(reply)
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise WorkIQError(f"WorkIQ {method}: {error}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise WorkIQError(f"WorkIQ returned an invalid result for {method}.")
            return result

    def request(self, method, params):
        with self._lock:
            try:
                if self._process is None:
                    self._start()
                return self._exchange(method, params)
            except (WorkIQError, OSError, ValueError) as ex:
                # Never replay a failed request: a write may already have taken effect.
                self._stop()
                if isinstance(ex, WorkIQError):
                    raise
                raise WorkIQError(f"WorkIQ connection failed: {ex}") from ex

    def _stop(self):
        process, self._process = self._process, None
        if process is None:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()

    def close(self):
        with self._lock:
            self._stop()


def _tool_output(result):
    text = [
        item["text"]
        for item in result.get("content", [])
        if item.get("type") == "text" and isinstance(item.get("text"), str)
    ]
    output = "\n".join(text)
    if not output and "structuredContent" in result:
        output = json.dumps(result["structuredContent"], ensure_ascii=False)
    if result.get("isError"):
        raise WorkIQError(output or "WorkIQ reported a tool error.")
    attachments = []
    for item in result.get("content", []):
        if item.get("type") in ("image", "audio"):
            attachments.append(
                llm.Attachment(
                    content=base64.b64decode(item["data"]),
                    type=item["mimeType"],
                )
            )
        elif item.get("type") == "resource_link":
            text = f"{item.get('name', 'Resource')}: {item['uri']}"
            output = f"{output}\n{text}" if output else text
        elif item.get("type") == "resource":
            resource = item.get("resource", {})
            text = resource.get("text", resource.get("uri", ""))
            output = f"{output}\n{text}" if output else text
    if attachments:
        return llm.ToolOutput(output=output, attachments=attachments)
    return output


class WorkIQ(llm.Toolbox):
    """Microsoft 365 tools from one persistent `workiq mcp` subprocess."""

    _blocked = llm.Toolbox._blocked + ("close",)

    def __init__(self, command="workiq", timeout=120, tools=None):
        if isinstance(command, str):
            command = [command]
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise ValueError("command must be an executable path or a non-empty argv list")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a positive number of seconds")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        if tools is not None and (
            not isinstance(tools, list) or any(not isinstance(name, str) for name in tools)
        ):
            raise ValueError("tools must be a list of MCP tool names")
        self._client = _StdioClient(command, timeout)
        self._allowed = None if tools is None else set(tools)
        self._definitions = None
        self._discovery_lock = threading.Lock()

    def _discover(self):
        with self._discovery_lock:
            if self._definitions is None:
                definitions = []
                params = {}
                cursors = set()
                while True:
                    page = self._client.request("tools/list", params)
                    definitions.extend(page.get("tools", []))
                    cursor = page.get("nextCursor")
                    if not cursor:
                        break
                    if cursor in cursors:
                        raise WorkIQError("WorkIQ repeated a tool-list pagination cursor.")
                    cursors.add(cursor)
                    params = {"cursor": cursor}
                if self._allowed is not None:
                    missing = self._allowed - {tool["name"] for tool in definitions}
                    if missing:
                        raise WorkIQError(
                            "WorkIQ does not expose the requested tools: "
                            + ", ".join(sorted(missing))
                        )
                self._definitions = definitions
            return self._definitions

    def _implementation(self, name):
        def call(**arguments):
            return _tool_output(
                self._client.request("tools/call", {"name": name, "arguments": arguments})
            )

        call.__name__ = name
        return call

    def tools(self):
        for definition in self._discover():
            name = definition["name"]
            if self._allowed is not None and name not in self._allowed:
                continue
            yield llm.Tool(
                name=f"WorkIQ_{name}",
                description=definition.get("description", ""),
                input_schema=definition.get("inputSchema", {"type": "object", "properties": {}}),
                implementation=self._implementation(name),
                plugin="llm_workiq",
            )
        yield from self._extra_tools

    def close(self):
        """Close the subprocess when using the toolbox from Python."""
        self._client.close()


@llm.hookimpl
def register_tools(register):
    register(WorkIQ)
