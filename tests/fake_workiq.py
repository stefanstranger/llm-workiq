"""A deterministic stdio MCP peer; never contacts Microsoft 365."""

import json
import sys
import time

log_path, behavior = sys.argv[1:3]
assert sys.argv[3] == "mcp"


def send(message):
    print(json.dumps(message), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps(request) + "\n")
    method = request.get("method")
    if method is None:
        continue
    if "id" not in request:
        continue
    result = {}
    if method == "initialize":
        if behavior == "bad-json":
            print("not JSON", flush=True)
            continue
        if behavior == "exit":
            sys.exit(0)
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-workiq", "version": "1"},
        }
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        if behavior == "ping":
            send({"jsonrpc": "2.0", "id": "server-ping", "method": "ping"})
            reply = json.loads(next(sys.stdin))
            assert reply == {"jsonrpc": "2.0", "id": "server-ping", "result": {}}
        if behavior == "rpc-error":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32000, "message": "Sign in required"},
                }
            )
            continue
        if "cursor" not in request["params"]:
            result = {
                "tools": [
                    {
                        "name": "fetch",
                        "description": "Fetch an entity",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"entityUrls": {"type": "array"}},
                            "required": ["entityUrls"],
                        },
                    }
                ],
                "nextCursor": "page2",
            }
        else:
            result = {
                "tools": [
                    {
                        "name": "ask",
                        "description": "Ask a work question",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"question": {"type": "string"}},
                        },
                    }
                ]
            }
            if behavior == "cursor-loop":
                result["nextCursor"] = "page2"
    elif method == "tools/call":
        if behavior == "timeout":
            time.sleep(2)
        elif behavior == "call-exit":
            sys.exit(0)
        arguments = request["params"]["arguments"]
        if arguments.get("question") == "error":
            result = {"isError": True, "content": [{"type": "text", "text": "Access denied"}]}
        elif arguments.get("question") == "structured":
            result = {"structuredContent": {"answer": "Structured answer"}}
        else:
            result = {
                "content": [{"type": "text", "text": json.dumps(arguments)}],
                "structuredContent": {"arguments": arguments},
            }
    send({"jsonrpc": "2.0", "id": request["id"], "result": result})
