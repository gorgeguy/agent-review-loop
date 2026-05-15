# Agent Review Loop v1 Plan

## Summary

Build a reusable local Python CLI project that coordinates two manually started
agent sessions: an editor and a reviewer. This is intentionally hub-and-spoke:
the broker owns durable state, turn enforcement, snapshots, and round limits;
agents are simple CLI clients that can be replaced or restarted at any time.

V1 supports Markdown and plain text documents. The editor edits the file
directly on disk. The broker records messages, rounds, hashes, and
content-addressed snapshots, but it does not apply patches itself.

The repository is scaffolded from `gorgeguy/python-template`, uses `uv` for
Python tooling, and uses Beads (`bd`) for all task tracking.

## Key Interfaces

- CLI entry point: `arl`
- Core commands:
  - `arl start --doc <path> [--max-rounds 5]`
  - `arl join --session <id>`
  - `arl init --doc <path> [--max-rounds 5]`
  - `arl serve --session <id>`
  - `arl prompt --role editor|reviewer --session <id>`
  - `arl next --role editor|reviewer --session <id> [--wait] [--context 5] [--full-transcript]`
  - `arl send --role editor|reviewer --session <id> --type <type> --body <text-or-file>`
  - `arl monitor --session <id> [--follow]`
- Diagnostic commands, if still cheap after the core loop lands:
  - `arl status --session <id>`
  - `arl transcript --session <id>`
- Message types:
  - `review_request`
  - `review_feedback`
  - `revision_report`
  - `approved`
  - `max_rounds_reached`
- Durable state:
  - sessions: document path, max rounds, current round, status, turn, socket path
  - document versions: round, file hash, snapshot path, timestamp
  - messages: role, type, round, body, timestamp
  - decisions: terminal status and reason
- Snapshot store:
  - Store document snapshots under `snapshots/<sha256>.md` or `snapshots/<sha256>.txt`.
  - Reuse an existing snapshot file when the same content hash already exists.

## Behavior

- `arl start` is the high-level editor bootstrap command. It requires an
  existing Markdown or text document, creates the session, starts the broker in
  the background, prints the session id, prints a short reviewer handoff that
  says to run `ARL_HOME=<state-dir> arl join --session <id>`, and then prints
  the editor prompt for the initiating agent to follow. The handoff is for the
  initiating agent to give to the user; the command output explicitly tells the
  initiating agent not to spawn, start, or run a reviewer itself unless the user
  asks.
- `arl join` is the high-level reviewer bootstrap command for an existing
  session. It implies the reviewer role and prints the reviewer prompt.
- `arl init` requires an existing Markdown or text document and records the
  initial hash and snapshot. Empty documents are allowed; the editor prompt tells
  the editor to draft content first when the file is empty.
- `arl serve` starts the broker in the foreground on a Unix-domain socket under
  a short deterministic temp directory. Durable state remains under the ARL home
  directory. The broker exits on terminal decision or SIGTERM.
- `arl prompt` prints a role-specific bootstrap prompt. A user pastes it into a
  fresh agent console to make that agent act as editor or reviewer.
- Generated prompts tell agents to run `arl next --wait` in a loop, perform work
  when it is their turn, send the required message, and stop when the broker
  reports a terminal state. Generated commands include `ARL_HOME=<state-dir>` so
  participants can run from different current directories.
- The editor prompt tells the editor not to act as reviewer or start reviewer
  agents. Reviewer bootstrap remains a user-mediated handoff in v1.
- `arl next` returns the pending action for the role when it is that role's
  turn. Without `--wait`, it returns immediately. With `--wait`, it blocks until
  work is available or the session reaches a terminal state.
- `arl next` includes the document path, round number, latest hash, latest
  snapshot path, the immediately prior message from the other role, and the last
  five session messages by default. `--context N` changes the message count;
  `--full-transcript` returns every message.
- `arl monitor` is a read-only observer command over durable state. It prints
  the current status, turn, round, latest document hash, latest snapshot path,
  and conversation messages. With `--follow`, it streams new messages until
  terminal state or interrupt.
- The editor sends `review_request` when ready for review. The broker hashes and
  snapshots the document at that point, increments or records the review round,
  and sets the turn to reviewer.
- The reviewer sends `review_feedback` or `approved`. Feedback sets the turn to
  editor. Approval records a terminal decision.
- The editor sends `revision_report` after processing feedback. The report must
  state what changed, what was not changed, and why. The broker hashes and
  snapshots the document, then sets the turn to reviewer.
- The broker enforces max rounds and records `max_rounds_reached` as a terminal
  decision. V1 does not include an `unresolvable` message; unresolved
  disagreement is handled by the round limit.

## Implementation Notes

- Keep v1 local-only; do not expose TCP or WebSocket transport.
- Use SQLite from the standard library for durable state.
- Store runtime socket files under a short deterministic temp path to avoid
  Unix socket path-length limits.
- Use Typer and Rich for the CLI.
- Use explicit state transitions; do not infer turn or session status from loose
  message text.
- Treat the document as untrusted data in generated prompts. When showing
  document content in prompts or responses, wrap it in clear delimiters such as
  `<DOCUMENT>` and `</DOCUMENT>`, and instruct agents to ignore instructions that
  appear inside the document body.
- V1 is not a hardened security boundary. The prompt-injection mitigation is a
  guardrail for cooperative local workflows, not a sandbox.

## Test Plan

- Unit tests for session creation, turn transitions, document hashing,
  snapshot creation, message persistence, pending-action selection, and terminal
  state transitions.
- Protocol tests for Unix socket JSONL request/response handling.
- CLI tests for `start`, `join`, `init`, `prompt`, `next`, `next --wait`,
  `send`, and `monitor`.
- End-to-end test with a temporary Markdown file simulating review request,
  feedback, revision report, second review, and approval.
- Resume test where a fresh editor or reviewer process calls `arl next` mid-loop
  and receives the correct pending action.
- Max-round test where the broker terminates the loop when the configured round
  limit is reached.
- Prompt-injection test proving document-body instructions are fenced and
  labeled as untrusted data in generated role prompts.
- Concurrent-send test proving an out-of-turn message is rejected and does not
  mutate session state.
- Monitor test proving read-only observation does not mutate session state and
  `--follow` exits cleanly when the session reaches a terminal decision.

## Assumptions And Defaults

- V1 is for one local machine and trusted local agent sessions.
- V1 supports Markdown and plain text files only.
- Editor and reviewer agents are manually started by the user in separate
  consoles, but each agent should self-loop with `arl next --wait` until the
  session ends.
- The broker coordinates state and protocol, but does not launch LLM agents.
- The default maximum is five review rounds.
- Beads issue IDs are for task tracking only and should not be referenced in
  maintained docs or commit messages.
