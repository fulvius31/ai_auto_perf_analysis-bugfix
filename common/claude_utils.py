import time
from dataclasses import dataclass, fields
from colorama import Fore, Style

from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ContentBlock,
    ResultMessage,
    SystemMessage,
    UserMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
)

from claude_agent_sdk.types import StreamEvent

from common.utils import clear_repo  # noqa: F401 - used via eval()

@dataclass
class ClaudeConfig:
    model: str
    allowed_tools: list[str]
    perm_mode: str
    cwd: str | None


def print_claude_config(config: ClaudeConfig) -> None:
    class_name = config.__class__.__name__
    print(f"{class_name}:")

    for field in fields(config):
        name = field.name
        value = getattr(config, name)
        print(f"  {name}: {value}")


def profile_system_prompt():
    return "You are a benchmarking and profiling expert of LLMs that run via vLLM, SGLang and TRT"


def print_dict(d, indent):
    for key, val in d.items():
        if isinstance(val, dict):
            print("{}{} : ".format(indent, key))
            print_dict(val, indent + "    ")
        else:
            print("{}{} : {}".format(indent, key, val))


def print_ContentBlock(prefix, content):
    if isinstance(content, TextBlock):
        print("{} {}".format(prefix, content.text))

    elif isinstance(content, ThinkingBlock):
        print("{} Thinking: {} {}".format(prefix, content.signature, content.thinking))

    elif isinstance(content, ToolUseBlock):
        print(
            "{} ToolUse: name = {}".format(
                prefix,
                content.name,
            )
        )

        print_dict(content.input, indent="    ")

    elif isinstance(content, ToolResultBlock):
        print(
            "{} ToolResult: err = {}".format(
                prefix,
                content.is_error,
            )
        )

        print()
        print(content.content)

    else:
        raise Exception("Unexpected type(content) = {}".format(type(content)))
    print()


def print_UserMessage(msg: UserMessage):
    prefix = f"{Fore.GREEN}User:{Style.RESET_ALL}"
    if isinstance(msg.content, str):
        print("{} {}".format(prefix, msg.content))
    else:
        for block in msg.content:
            assert isinstance(block, ContentBlock)
            print_ContentBlock(prefix, block)


def print_AssistantMessage(msg: AssistantMessage):
    prefix = f"{Fore.CYAN}Claude:{Style.RESET_ALL}"

    for block in msg.content:
        assert isinstance(block, ContentBlock)
        print_ContentBlock(prefix, block)

    if msg.error is not None:
        print("    -- ERROR = {}".format(msg.error))


def print_SystemMessage(msg: SystemMessage):
    print("System: subtype = {} data = {}".format(msg.subtype, msg.data))


def print_ResultMessage(msg: ResultMessage):
    print(
        "Result: subtype = {} dur = {} dur_api = {} err = {} num_turns = {}".format(
            msg.subtype,
            msg.duration_ms,
            msg.duration_api_ms,
            msg.is_error,
            msg.num_turns,
        )
    )

    if msg.usage:
        print("    -- usage = {}".format(msg.usage))

    if msg.result is not None:
        print("    -- result = {}".format(msg.result))


def print_StreamEvent(msg: StreamEvent):
    print("Stream: event = {}".format(msg.event))


def print_Message(msg):
    if isinstance(msg, UserMessage):
        print_UserMessage(msg)
    elif isinstance(msg, AssistantMessage):
        print_AssistantMessage(msg)
    elif isinstance(msg, SystemMessage):
        print_SystemMessage(msg)
    elif isinstance(msg, ResultMessage):
        print_ResultMessage(msg)
    elif isinstance(msg, StreamEvent):
        print_StreamEvent(msg)
    else:
        raise Exception("Unexpected type(msg) = {}".format(type(msg)))


async def claude_run(claude_config: ClaudeConfig, prompts: list[str], tracker=None):
    assert claude_config.cwd is not None, "claude_config must have CWD set"

    print_claude_config(claude_config)

    options = ClaudeAgentOptions(
        system_prompt=profile_system_prompt(),
        allowed_tools=claude_config.allowed_tools,
        permission_mode=claude_config.perm_mode,
        cwd=claude_config.cwd,
        env={
            "ANTHROPIC_MODEL": claude_config.model,
            "MAX_THINKING_TOKENS": "1048576",
        },
        model=claude_config.model,
        thinking={"type": "adaptive"},
        effort="max",
    )

    async with ClaudeSDKClient(options=options) as client:
        prompt_step = 0
        for i, prompt in enumerate(prompts):
            if isinstance(prompt, dict):
                cmd = prompt["cmd"]
                print("{} RUN CMD: {} {}".format(Fore.GREEN, cmd, Style.RESET_ALL))
                eval(cmd)
                continue

            prompt_step += 1
            start_time = time.time()

            prompt_name = ""
            if isinstance(prompt, str):
                for marker in ["Phase 0", "Phase 1", "Phase 2", "Phase 3", "Phase 4",
                               "Triage Agent", "Test Port Agent", "Cherry-Pick Agent",
                               "Narrow Resolution Agent", "Fix Agent", "Quorum"]:
                    if marker.lower() in prompt[:200].lower():
                        prompt_name = marker
                        break
                if not prompt_name:
                    prompt_name = f"query_{prompt_step}"

            print("{} SEND QUERY PROMPT:{}".format(Fore.GREEN, Style.RESET_ALL))
            print(
                "{}============================================{}".format(
                    Fore.GREEN, Style.RESET_ALL
                )
            )
            print(prompt)
            print(
                "{}============================================{}".format(
                    Fore.CYAN, Style.RESET_ALL
                )
            )
            print("{} RESPONSES:{}".format(Fore.CYAN, Style.RESET_ALL))
            print(
                "{}============================================{}".format(
                    Fore.CYAN, Style.RESET_ALL
                )
            )

            result_data = {}
            await client.query(prompt)
            async for msg in client.receive_response():
                print_Message(msg)
                if isinstance(msg, ResultMessage):
                    # Extract usage from model_usage dict (per-model breakdown with camelCase keys)
                    model_usage = msg.model_usage or {}

                    # Get the usage for the current model (keyed by model name in model_usage)
                    model_stats = {}
                    if model_usage and claude_config.model in model_usage:
                        model_stats = model_usage[claude_config.model]

                    input_tokens = model_stats.get("inputTokens", 0) or 0
                    output_tokens = model_stats.get("outputTokens", 0) or 0
                    cache_read_tokens = model_stats.get("cacheReadInputTokens", 0) or 0

                    result_data = {
                        "duration_api_ms": msg.duration_api_ms or 0,
                        "num_turns": msg.num_turns or 0,
                        "is_error": msg.is_error or False,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read_tokens": cache_read_tokens,
                    }

                    # Log extracted tokens
                    print(f"Extracted tokens: in={input_tokens}, out={output_tokens}, cache={cache_read_tokens}")
                print(
                    "{}--------------------------------------------{}".format(
                        Fore.CYAN, Style.RESET_ALL
                    )
                )
            print(
                "{}============================================{}".format(
                    Fore.CYAN, Style.RESET_ALL
                )
            )

            end_time = time.time()
            duration_time = end_time - start_time
            mins, secs = divmod(int(duration_time), 60)
            hrs, mins = divmod(mins, 60)
            human_time = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"
            print("FINISHED STEP {}: duration = {:.1f}s ({})".format(prompt_step, duration_time, human_time))

            if tracker is not None:
                tracker.record_query(
                    prompt_name=prompt_name,
                    start_time=start_time,
                    end_time=end_time,
                    **result_data,
                )
