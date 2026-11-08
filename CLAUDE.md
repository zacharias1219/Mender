@AGENTS.md

## Claude Code

- AGENTS.md above holds the shared rules and the project reference. Put new project facts
  there, not here, so every agent sees them. Keep this file for Claude-only notes.
- At the end of each session, update the AGENTS.md status log and open items, then commit
  and push. Cloud containers are deleted, and unpushed work is lost.
- Read the spec in `docs/` that covers the current step before writing its code.
- Secrets: never ask for the Groq key in chat. Use the secure credential form for
  `api.groq.com`. Never run commands that echo `$GROQ_API_KEY`. For the G10 check, use
  `grep -rqF -- "$GROQ_API_KEY" runs data && echo LEAK || echo clean`.
- Before saying a step is done, paste the real acceptance-command output (trimmed) into
  the reply.
