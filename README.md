# Agent Review Loop

Agent Review Loop coordinates a local iterative document review between two
manually started AI agents: an editor and a reviewer. A small broker enforces
turn order and round limits while SQLite keeps enough state for fresh agents to
resume the conversation.

V1 supports one Markdown or plain text document per session.

## Setup

```bash
make install
```

State is stored in `.arl/` under the current working directory by default. Set
`ARL_HOME` to choose another state directory.

## Local Workflow

Start a session from the editor agent console:

```bash
arl start --doc ./draft.md --max-rounds 5
```

The command creates the session, starts the broker in the background, prints the
session id, and prints a short reviewer handoff for the initiating agent to give
to the user:

```bash
ARL_HOME=/path/to/.arl arl join --session <session-id>
```

After printing that reviewer handoff, `arl start` prints the editor prompt for
the initiating agent to follow. Background broker logs are written under
`ARL_HOME/logs/`. The generated commands include `ARL_HOME` explicitly so the
editor, reviewer, and observer do not need to be in the same current directory.
The start output explicitly tells the initiating agent not to run the reviewer
handoff or spawn a reviewer itself unless the user asks.

Reviewer agent terminal:

```bash
ARL_HOME=/path/to/.arl arl join --session <session-id>
```

Paste the generated prompt into the reviewer agent. The reviewer loops on
`arl next --wait` until the session ends.

Observer terminal:

```bash
ARL_HOME=/path/to/.arl arl monitor --session <session-id> --follow
```

`monitor`, `status`, and `transcript` are read-only views over durable state and
do not affect turn order. `monitor` and `transcript` default to JSON for agents
and scripts; add `--human` for readable terminal output:

```bash
ARL_HOME=/path/to/.arl arl monitor --session <session-id> --human
ARL_HOME=/path/to/.arl arl transcript --session <session-id> --human
```

## Lower-Level Workflow

The high-level commands wrap the lower-level primitives. To run the broker in an
explicit foreground terminal instead, create a session manually:

```bash
arl init --doc ./draft.md --max-rounds 5
```

The command prints JSON containing the session id. Start the broker:

```bash
arl serve --session <session-id>
```

The broker runs in the foreground. It exits when the session is approved, the
maximum round count is reached, or the process receives an interrupt/terminate
signal.

Generate role prompts directly:

```bash
arl prompt --role editor --session <session-id>
arl prompt --role reviewer --session <session-id>
```

## Agent Message Commands

The editor sends a contextual review request after drafting or editing:

```bash
arl send --role editor --session <session-id> --type review_request --body "Fresh-eyes review: I tightened the implementation plan. Please look for bad assumptions, missing failure cases, and places where the approach should change before more work is spent."
```

After reviewer feedback, the editor sends a revision report:

```bash
arl send --role editor --session <session-id> --type revision_report --body "Changed X. Did not change Y because Z."
```

The generated editor prompt tells the editor to immediately return to
`arl next --role editor --wait` after sending a review request or revision
report, so reviewer feedback is picked up without a user reminder.

The reviewer sends feedback:

```bash
arl send --role reviewer --session <session-id> --type review_feedback --body "Please tighten the opening."
```

Or approves:

```bash
arl send --role reviewer --session <session-id> --type approved --body "APPROVED"
```

## Storage And Snapshots

Durable state lives in SQLite under `ARL_HOME`. Document snapshots are stored as
content-addressed files under `ARL_HOME/snapshots/`. Runtime Unix socket files
live in a short deterministic directory under the system temp directory to avoid
platform socket path-length limits.

The editor edits the document directly on disk. The broker hashes and snapshots
the document when the editor sends `review_request` or `revision_report`.

## Safety Limits

The generated prompts fence document contents between `<DOCUMENT>` and
`</DOCUMENT>` and instruct agents to treat that content as untrusted data. This
is a cooperative guardrail, not a hardened security boundary.

V1 is local-only. It does not launch LLM agents, expose network sockets, support
rich document formats, or coordinate multiple documents in one session.

## Development

```bash
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen pyright
make ci
```

Use Beads for task tracking:

```bash
bd ready
bd show <id>
bd update <id> --claim
bd close <id>
```
