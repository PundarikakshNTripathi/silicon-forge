@AGENTS.md

## Claude Code specifics
- Project slash commands live in `.claude/commands/` (`/sf-test`, `/sf-demo`); the `sf-eda-reviewer` subagent in
  `.claude/agents/` reviews guardrail/EDA changes. Shared skills are in `.agents/skills/` (read them when the task matches).
- Prefer the dedicated Read/Grep/Edit tools over shell equivalents. Use plan mode for changes spanning pipeline + API + UI.
