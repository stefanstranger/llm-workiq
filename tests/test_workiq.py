import base64
import json
import sys
from pathlib import Path

import llm
import pytest
from click.testing import CliRunner
from llm.cli import cli
from llm.utils import instantiate_from_spec

from llm_workiq import WorkIQ, WorkIQError, _tool_output

SERVER = Path(__file__).with_name("fake_workiq.py")


@pytest.fixture
def toolbox(tmp_path):
    instances = []

    def create(behavior="normal", **kwargs):
        log = tmp_path / f"requests-{len(instances)}.jsonl"
        box = WorkIQ(
            command=[sys.executable, str(SERVER), str(log), behavior],
            **kwargs,
        )
        instances.append(box)
        return box, log

    yield create
    for box in instances:
        box.close()


def requests(log):
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_discovers_all_pages_preserves_schemas_and_reuses_process(toolbox):
    box, log = toolbox()
    tools = list(box.tools())
    assert [tool.name for tool in tools] == ["WorkIQ_fetch", "WorkIQ_ask"]
    assert tools[0].input_schema["required"] == ["entityUrls"]
    assert tools[0].description == "Fetch an entity"
    assert json.loads(tools[0].implementation(entityUrls=["/me"])) == {"entityUrls": ["/me"]}
    assert json.loads(tools[1].implementation(question="hello")) == {"question": "hello"}
    assert [tool.name for tool in box.tools()] == ["WorkIQ_fetch", "WorkIQ_ask"]
    calls = requests(log)
    assert [call["method"] for call in calls] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/list",
        "tools/call",
        "tools/call",
    ]
    assert calls[3]["params"] == {"cursor": "page2"}
    assert calls[-1]["params"] == {"name": "ask", "arguments": {"question": "hello"}}
    process = box._client._process
    box.close()
    assert process.poll() is not None
    box.close()


def test_tool_allowlist_and_no_extra_public_methods(toolbox):
    box, _ = toolbox(tools=["fetch"])
    assert [tool.name for tool in box.tools()] == ["WorkIQ_fetch"]
    empty, _ = toolbox(tools=[])
    assert list(empty.tools()) == []


def test_unknown_tool_fails_explicitly(toolbox):
    box, _ = toolbox(tools=["does_not_exist"])
    with pytest.raises(WorkIQError, match="does_not_exist"):
        list(box.tools())


def test_structured_results_and_tool_errors(toolbox):
    box, _ = toolbox()
    tool = list(box.tools())[1]
    assert json.loads(tool.implementation(question="structured")) == {"answer": "Structured answer"}
    with pytest.raises(WorkIQError, match="Access denied"):
        tool.implementation(question="error")
    assert json.loads(tool.implementation(question="after error")) == {"question": "after error"}


@pytest.mark.parametrize(
    ("behavior", "message"),
    [
        ("bad-json", "invalid JSON"),
        ("exit", "closed its MCP connection"),
        ("rpc-error", "Sign in required"),
        ("cursor-loop", "pagination cursor"),
    ],
)
def test_discovery_errors(toolbox, behavior, message):
    box, _ = toolbox(behavior)
    with pytest.raises(WorkIQError, match=message):
        list(box.tools())


def test_server_ping_during_discovery(toolbox):
    box, _ = toolbox("ping")
    assert len(list(box.tools())) == 2


@pytest.mark.parametrize("behavior", ["timeout", "call-exit"])
def test_failed_calls_are_not_replayed_and_next_request_reconnects(toolbox, behavior):
    box, log = toolbox(behavior, timeout=1)
    tool = list(box.tools())[1]
    with pytest.raises(WorkIQError, match="timed out|closed its MCP connection"):
        tool.implementation(question="do not replay")
    calls = requests(log)
    assert sum(call["method"] == "tools/call" for call in calls) == 1
    assert box._client._process is None
    box._client.command[-1] = "normal"
    assert json.loads(tool.implementation(question="new request")) == {"question": "new request"}
    assert sum(call["method"] == "initialize" for call in requests(log)) == 2


def test_missing_executable_has_actionable_error():
    box = WorkIQ(command="nonexistent-workiq-test-executable")
    try:
        with pytest.raises(WorkIQError, match="Install the official WorkIQ CLI"):
            list(box.tools())
    finally:
        box.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"command": []},
        {"command": [1]},
        {"timeout": 0},
        {"timeout": float("inf")},
        {"timeout": True},
        {"tools": "fetch"},
    ],
)
def test_invalid_options(kwargs):
    with pytest.raises(ValueError):
        WorkIQ(**kwargs)


def test_llm_toolbox_spec_and_plugin_registration():
    box = instantiate_from_spec({"WorkIQ": WorkIQ}, 'WorkIQ(tools=["fetch"], timeout=300)')
    try:
        assert box._allowed == {"fetch"}
        assert box._client.timeout == 300
        assert llm.get_tools()["WorkIQ"] is WorkIQ
    finally:
        box.close()


def test_llm_cli_discovers_tools_from_stdio(tmp_path):
    log = tmp_path / "cli-requests.jsonl"
    command = [sys.executable, str(SERVER), str(log), "normal"]
    spec = f"WorkIQ(command={json.dumps(command)})"
    result = CliRunner().invoke(cli, ["tools", "list", spec, "--json"])
    assert result.exit_code == 0, result.output
    discovered = json.loads(result.output)
    assert {tool["name"] for tool in discovered["toolboxes"][0]["tools"]} == {
        "WorkIQ_fetch",
        "WorkIQ_ask",
    }


def test_llm_model_chain_calls_workiq_and_receives_result(toolbox):
    class LocalModel(llm.Model):
        model_id = "local-workiq-test"
        supports_tools = True

        def execute(self, prompt, stream, response, conversation):
            if prompt.tool_results:
                yield prompt.tool_results[0].output
            else:
                response.add_tool_call(
                    llm.ToolCall(name="WorkIQ_ask", arguments={"question": "test question"})
                )
                yield ""

    box, log = toolbox(tools=["ask"])
    model = LocalModel()
    conversation = model.conversation(tools=[box])
    assert json.loads(conversation.chain("first turn").text()) == {"question": "test question"}
    assert json.loads(conversation.chain("second turn").text()) == {"question": "test question"}
    calls = requests(log)
    assert sum(call["method"] == "initialize" for call in calls) == 1
    assert sum(call["method"] == "tools/call" for call in calls) == 2


def test_image_audio_and_resource_output():
    data = base64.b64encode(b"example attachment").decode()
    result = _tool_output(
        {
            "content": [
                {"type": "text", "text": "Result"},
                {"type": "image", "data": data, "mimeType": "image/png"},
                {"type": "audio", "data": data, "mimeType": "audio/wav"},
                {"type": "resource_link", "name": "Document", "uri": "https://example.com/doc"},
                {"type": "resource", "resource": {"uri": "file:///example", "text": "Excerpt"}},
            ]
        }
    )
    assert isinstance(result, llm.ToolOutput)
    assert result.output == "Result\nDocument: https://example.com/doc\nExcerpt"
    assert [attachment.type for attachment in result.attachments] == ["image/png", "audio/wav"]
    assert result.attachments[0].content == b"example attachment"
