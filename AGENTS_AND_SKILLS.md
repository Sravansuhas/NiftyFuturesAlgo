# Aegis — Agents & Skills — How We Build & Evolve

**Purpose**: Explain how the project will use Grok Build's latest capabilities (skills, subagents, personas, plan-mode, bundled loops, MCP) as the primary "easy to build and deploy" mechanism. This is both documentation and a contributor/founder operating manual.

**Last Updated**: 2026-05 (vision lock)

---

## Why Agents & Skills Matter Here

Retail F&O trading destroys people because it is emotional, ad-hoc, and lacks process.

We are building the opposite: a system whose own *evolution* is systematic, reviewed, auditable, and fast — using the best agentic tooling available (Grok 4.3 in 2026).

The goal is not to have the AI trade for us blindly. The goal is to have the AI do the heavy lifting of research, proposal generation, validation orchestration, and documentation — while humans (founder + reviewers) retain final authority on anything that touches risk or real capital.

---

## Grok Build Primitives We Will Use (Latest Methods)

### 1. Skills (Reusable, Triggerable Workflows)

Skills are directories with `SKILL.md` (frontmatter + instructions). They let us encode repeatable procedures so we don't re-explain context every time.

**Project Scope (Highest Priority)**:
- Location: `.grok/skills/fo-*` inside this repo.
- These are versioned with the code.
- Activated automatically when the description matches user (or agent) intent.

**Planned Custom Skills (Initial Set)**

| Skill Name              | Trigger / Slash Command          | What It Does (High Level)                                                                 | When to Use |
|-------------------------|----------------------------------|-------------------------------------------------------------------------------------------|-------------|
| fo-market-brief         | /fo-market-brief                 | Runs regime snapshot + refreshes failure pattern knowledge + produces morning report     | Every trading morning (or on-demand) |
| fo-failure-pattern-miner| /fo-failure-pattern-miner        | Searches recent Reddit + public sources, extracts new anti-patterns, proposes filter updates | Weekly or after major events |
| fo-strategy-proposer    | /fo-strategy-proposer            | Uses best-of-n + design skill to generate 3–5 candidate variants, routes them through WFA | When we want new ideas or mutations |
| fo-safe-deploy          | /fo-safe-deploy                  | Full pre-deployment checklist (recon clean, token valid, risk params, human gate) | Before any paper size increase or live enable |
| fo-validate-index       | /fo-validate-banknifty (example) | Runs full 6–12 month WFA + MC + 2x cost sensitivity on a specific index                   | When expanding or re-validating |
| fo-knowledge-update     | /fo-knowledge-update             | Updates INDIAN_FO_KNOWLEDGE_BASE.md + memory from agent output                            | After miner runs |

**How to Create New Skills**:
- Use `/create-skill` (or the bundled create-skill skill) interactively.
- Prefer project scope for anything trading-specific.
- Follow the exact SKILL.md frontmatter format (name + description is critical for auto-invocation).

### 2. Subagents & Personas

Subagents run in parallel with their own context.

**Built-in Types We Use**:
- `explore` — market research, Reddit mining, codebase archaeology.
- `plan` — architecture and roadmap work (this document was created in plan mode).
- `general-purpose` — most work, usually with a persona layered on top.

**Personas (Behavioral Overlays)** — from `~/.grok/bundled/skills/shared/personas/`:
- `implementer` — proactive, clean code, writes summaries.
- `reviewer` — thorough, finds bugs/suggestions/nits, structured output.
- `security-auditor` — OWASP + trading-specific (secrets, injection in order paths, risk bypasses).
- `design-doc-writer` / `design-doc-reviewer`

**Usage Pattern**:
```python
# Example (in a skill or manual)
spawn_subagent(
    subagent_type="general-purpose",
    description="[implementer] Implement dynamic instruments manager for 3 indices",
    # persona injected via prompt prefix in practice
)
```

### 3. Bundled High-Rigor Skills (Use These for All Changes)

These are the "latest methods" that make the evolution safe:

- **/implement --effort N** (N=1 to 5)
  - Full implement → review → fix loop.
  - Effort 3–5 recommended for anything touching trading logic (multiple reviewers + security + tests + plan alignment + memory of past issues).
  - Never ends until 0 open issues of any severity.
  - Produces review files, memory updates, and auditable artifacts.

- **/design**
  - Full design-doc + reviewer loop.
  - Use for every new agent, major module, or architecture change.

- **/review**
  - Standalone review of local changes or PRs (with security-auditor persona when appropriate).

- **/execute-plan**
  - Takes a design doc, turns it into a DAG of tasks, runs parallel worktree implementations + mandatory orchestrator review.
  - Perfect for roadmap phases.

- **best-of-n**
  - Run N different implementations or strategy variants in parallel, pick the winner after evaluation.

- **create-skill** + **/skillify**
  - Capture workflows that emerge during work.

### 4. MCP Servers (External Tooling)

We already have `grok_com_github` connected (43 tools: create_issue, create_pull_request, search_code, list_pull_requests, etc.).

**Usage**:
- Agents can create GitHub issues for "new failure pattern discovered — needs filter".
- Agents can draft PRs from generated patches (founder still reviews/merges).
- Search prior discussions before proposing changes.

### 5. Plan Mode & Agent Mode

- **plan-mode** (current): Used for all major vision and architecture work (this entire documentation effort).
- **agent-mode (ACP)**: For deep IDE-integrated coding sessions when doing complex implementation.

---

## Operating Rhythm (Founder + Agents)

**Daily / Morning**:
- Start system → open dashboard.
- (Future) Run `/fo-market-brief` → read the report in terminal or dashboard.

**Weekly**:
- Run failure pattern miner.
- Review memory + documentation notes.
- Decide on any parameter or filter tweaks (via proper /implement or /design process).

**When Adding Capability**:
1. Write or update design doc (use /design skill).
2. Implement via /implement --effort 3-5 (with trading/security reviewers).
3. Update relevant .md files (VISION, KNOWLEDGE_BASE, ARCHITECTURE, etc.).
4. If new repeatable workflow → capture as skill via create-skill or /skillify.
5. Use MCP to open issue/PR if the change is significant.

**Never**:
- Manually edit trading logic without going through the review loops (the whole point is to avoid the ad-hoc mistakes retail traders make).
- Bypass RiskGatekeeper.
- Deploy to paper with increased risk without running the safe-deploy checklist.

---

## Project .grok/ Layout (Target)

```
.grok/
├── skills/
│   ├── fo-market-brief/
│   │   └── SKILL.md
│   ├── fo-failure-pattern-miner/
│   │   └── SKILL.md
│   └── ...
├── agents/          # optional custom agent definitions
└── config.toml      # local overrides if needed
```

Skills in `.grok/skills/` (project) override user-scope ones.

**Current Status (June 2026):** Initial activation complete. `fo-market-brief` and `fo-failure-pattern-miner` skeletons exist and are registered. Next: make the brief skill actually read `backtest_memory` + produce output, then wire it into the dashboard "Agent Insights" area. All future skills must follow the same `/create-skill` or manual SKILL.md process.

---

## Getting Started as Contributor or Founder

1. Read VISION_AND_STRATEGY.md + this file.
2. Run the existing system (`python run.py`) and explore the dashboard + backtest lab.
3. **For closed-market development**: Use `python run.py --dev` (see `docs/DEV_TESTING_GUIDE.md`).
4. For any change: start in plan mode or use /design.
5. For implementation: always use /implement with appropriate effort.
6. When you discover a repeatable process: use /skillify or create-skill to capture it.

---

The agents and skills layer is how we turn a great piece of trading *infrastructure* into a self-improving *intelligence platform* without losing the discipline that makes the infrastructure trustworthy.

This is the meta-edge: we use the best agentic development process to build the best risk-aware trading process.

---

*Update this document whenever new skills are added or the Grok Build tooling evolves.*