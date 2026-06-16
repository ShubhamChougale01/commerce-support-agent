"""LLM client factory — Anthropic API key OR Claude Code subscription.

Both prompt modules (classifier, reply_generator) call a Messages-API-style
client:

    resp = await client.messages.create(
        model=..., max_tokens=..., system=..., messages=[{role, content}],
    )
    text = resp.content[0].text

`get_client()` returns whichever backend is available:

* If ANTHROPIC_API_KEY is set  -> the real `anthropic.AsyncAnthropic` (unchanged
  production path).
* Otherwise                    -> `AgentSDKAnthropic`, a thin shim that routes
  the same call through the Claude Agent SDK (`claude-agent-sdk`), which
  authenticates via the local `claude` CLI subscription — no API key needed.

Only the narrow slice of the SDK this project uses is implemented. The shim is
for local/dev runs on a Claude Code subscription; production should use an
API key (see SETUP_GUIDE / BLOCKERS — subscription auth for an automated
backend is not an approved Anthropic use case).
"""

from dataclasses import dataclass

from app.config import ANTHROPIC_API_KEY


@dataclass
class _TextBlock:
    text: str


@dataclass
class _Response:
    content: list  # list[_TextBlock] — mirrors anthropic Message.content


class _Messages:
    """Implements `.create(...)` with the same signature/return shape as
    `anthropic.AsyncAnthropic().messages`."""

    async def create(
        self,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
        messages: list | None = None,
        **_ignored,
    ) -> _Response:
        # Lazy import so a missing claude-agent-sdk only matters on this path.
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            TextBlock,
            query,
        )

        # This project only sends user-role turns (the classifier retry adds a
        # second user message). Flatten them into one prompt string.
        parts: list[str] = []
        for m in messages or []:
            content = m.get("content")
            if isinstance(content, list):  # content blocks -> join their text
                content = "".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
            parts.append(str(content))
        prompt = "\n\n".join(parts)

        options = ClaudeAgentOptions(
            system_prompt=system,        # plain str REPLACES the default preset
            model=model,
            max_turns=1,                 # single model call, no agent loop
            allowed_tools=[],            # behave like a plain completion
            setting_sources=[],          # ignore project CLAUDE.md / settings
            permission_mode="bypassPermissions",
        )

        text = ""
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text += block.text
        return _Response(content=[_TextBlock(text=text)])


class AgentSDKAnthropic:
    """Minimal stand-in for `anthropic.AsyncAnthropic` backed by the Agent SDK."""

    def __init__(self, *_args, **_kwargs):
        self.messages = _Messages()


def get_client():
    """Return the active LLM client: real Anthropic SDK if an API key is
    configured, otherwise the Claude Agent SDK subscription shim."""
    if ANTHROPIC_API_KEY:
        from anthropic import AsyncAnthropic

        return AsyncAnthropic()
    return AgentSDKAnthropic()
