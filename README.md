---
title: llm-workiq
description: Use Microsoft WorkIQ tools with the LLM command-line tool.
---

Use Microsoft WorkIQ's Microsoft 365 tools with
[LLM](https://llm.datasette.io/), Simon Willison's command-line tool and Python
library.

The plugin launches the official `workiq mcp` server, discovers its tools,
and keeps one stdio connection open for the life of the toolbox. WorkIQ
handles Microsoft authentication and access to your tenant. No HTTP bridge,
local port, or custom token handling is required.

## Prerequisites

- Python 3.10 or later and LLM 0.36 or later.
- The official [Microsoft WorkIQ CLI](https://github.com/microsoft/work-iq)
  installed and available as `workiq` on your PATH.
- A Microsoft 365 account with the access, licensing, and tenant consent
  required by your WorkIQ deployment.

## Install

Install from [PyPI](https://pypi.org/project/llm-workiq/):

```powershell
llm install llm-workiq
```

Or install directly from this repository:

```powershell
llm install git+https://github.com/stefanstranger/llm-workiq.git
```

From a local checkout, run:

```powershell
llm install -e .
```

Python dependencies are installed automatically. Install the WorkIQ CLI
separately using its official instructions.

## Authenticate

Sign in using WorkIQ before using this plugin:

```powershell
workiq auth login
```

The available WorkIQ versions and authentication commands can differ by
distribution. Use the official installation instructions for your tenant;
check `workiq --help` if `auth login` is not available.

## Use

Replace `your-tool-capable-model` with a configured model that supports tool
calling. Use a provider approved for your workplace data: WorkIQ results
are sent to the model provider you select.

```powershell
llm -m your-tool-capable-model -T WorkIQ "What meetings do I have tomorrow?" --td --ta
llm chat -m your-tool-capable-model -T WorkIQ --ta
```

`--ta` (`--tools-approve`) asks you to approve each tool execution.
`--td` (`--tools-debug`) prints tool calls and results, which can include
sensitive workplace data. Omit `--td` when you do not need that output.

Offering tools does not force the model to call them. The model decides
whether a tool is relevant and may answer directly instead.

Do **not** start `workiq mcp` in another terminal. The toolbox starts and
owns its subprocess. It does not connect directly to
`https://workiq.svc.cloud.microsoft/mcp`: the local WorkIQ CLI manages that
authenticated connection.

### Use with Microsoft Foundry

Install the
[llm-microsoft-foundry plugin](https://github.com/stefanstranger/llm-microsoft-foundry)
and follow its authentication and model configuration instructions. Enable
tool support with `--tools` when configuring a tool-capable chat deployment.

For a model configured with the ID `foundry-gpt-5.4`, run:

```powershell
llm -m foundry-gpt-5.4 -T WorkIQ "What meetings do I have tomorrow?" --td --ta
```

The Foundry plugin provides the model connection; this plugin provides the
WorkIQ tools.

### Inspect or restrict tools

List the tools without sending a prompt to a model:

```powershell
llm tools list WorkIQ
```

All tools advertised by your WorkIQ server are exposed by default, including
write tools if your server provides them. Restrict the toolbox to selected
MCP tool names when appropriate:

```powershell
llm -m your-tool-capable-model -T 'WorkIQ(tools=["ask", "fetch"])' "What meetings do I have tomorrow?" --ta
```

Tool names shown to the model have a `WorkIQ_` prefix, for example
`WorkIQ_fetch`. The `tools` option takes the original, unprefixed MCP names.
An unavailable tool name produces an error instead of being silently ignored.

### Options

| Option    | Default     | Description                                    |
|-----------|-------------|------------------------------------------------|
| `command` | `"workiq"`  | Executable path or argv list, without `mcp`     |
| `timeout` | `120`       | Timeout in seconds per MCP request             |
| `tools`   | All tools   | Allow-list of original, unprefixed MCP names    |

### Executable and timeout

The default executable is `workiq` and the timeout is 120 seconds per MCP
request. Increase it for long-running agentic queries:

```powershell
llm -m your-tool-capable-model -T 'WorkIQ(timeout=300)' "Summarize this week''s project discussions" --ta
```

On Windows, the plugin resolves npm's `workiq.cmd` shim. You can also specify
an explicit executable, using escaped backslashes in toolbox JSON strings:

```powershell
llm -m your-tool-capable-model -T 'WorkIQ(command="C:\\Users\\you\\AppData\\Roaming\\npm\\workiq.cmd")' "List my meetings" --ta
```

`command` can be an argv list, such as `["node", "C:\\path\\to\\workiq.js"]`.
The plugin appends `mcp` automatically. It does not interpret shell command
strings.

### Python

```python
import llm
from llm_workiq import WorkIQ

workiq = WorkIQ(tools=["ask", "fetch"])
try:
    model = llm.get_model("your-tool-capable-model")
    response = model.chain("What meetings do I have tomorrow?", tools=[workiq])
    print(response.text())
finally:
    workiq.close()
```

## Behavior and data handling

- Tool discovery is cached; tool calls share the same subprocess and are
  serialized. Both ordinary prompts and interactive chat can reuse it.
- The plugin supports MCP initialization, paginated tool discovery,
  notifications, server pings, tool calls, text/structured results, and
  image/audio results when the server provides them. It is not a general
  MCP client for resources, prompts, sampling, or elicitation.
- A timeout or connection failure closes the subprocess. The failed
  operation is **not automatically replayed**, because a write might already
  have taken effect. A subsequent request can start a fresh connection.
- WorkIQ tool errors are returned to LLM as exceptions, not successful text.
- Subprocesses close at interpreter exit; Python callers can close them
  explicitly.
- WorkIQ results are sent to the model provider you select. LLM may also
  record tool calls and results in its local logs. Use a provider approved
  for your work data, and configure LLM logging as needed.
- The plugin adds no approval UI of its own. Use LLM's `--ta` option to
  approve each tool execution. Enabling write tools permits the model to
  request those actions; use a tool allow-list if you only need reads.

## Development

```powershell
llm install -e .
uv sync --locked --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

Tests use a fake local MCP subprocess. They require no Microsoft account,
network access, or model API key.

## Publishing

See the [publishing guide](PUBLISHING.md) for release setup and maintainer
instructions.

## License

Apache-2.0. This is an independent integration, not an official Microsoft
product.
