---
name: correction-from-issue
description: |
  Use when the user wants to turn a feedback GitHub Issue (filed via the in-app "Report this answer" flow) into a corrections markdown file under `deploy/sources/corrections/`. Triggers on phrases like "process feedback issue #N", "create a correction from issue N", "/correction-from-issue N", or any request that names a feedback issue number and asks for a fix. Walks the user from issue → parsed body → scaffolded correction file → committed change → closed GH issue, pausing for them to refine the authoritative answer before commit. Refuses cleanly if the issue lacks the `feedback` label.
argument-hint: "<issue-number>"
---

# Correction from Feedback Issue

Turn one feedback GitHub Issue into a curated correction markdown file. Walk the user through it — they refine the answer text, then we commit and close the issue.

## Prerequisites (check before doing anything)

1. `gh auth status` — the GitHub CLI must be authenticated. If not, stop and tell the user to run `gh auth login` and retry.
2. The current working directory is the project repo (has `deploy/sources/`).
3. The `corrections/` ingester may NOT yet be built. If it isn't, the file still gets created — the dev resyncs once the ingester ships. Note this to the user at the end; don't block the workflow.

## Input

`$ARGUMENTS` is the issue number. Strip a leading `#` if present. If it's empty or non-numeric, stop and ask the user for a number.

## Workflow

### 1. Fetch and validate the issue

```bash
gh issue view <N> --json number,title,body,labels,state,url
```

Reject and stop if:
- The issue is **closed** — corrections shouldn't be re-applied; tell the user and offer to reopen if they're sure.
- The labels do **not** include `feedback` — this skill is only for in-app feedback issues. Tell the user the label is missing and suggest they add it or process the issue manually.

### 2. Parse the body

The body was assembled server-side by `app/backend/services/github.py` and has a fixed structure. Sections are H2 headers in this order:

```
## User question
<one or more paragraphs>

## Assistant answer
<one or more paragraphs>

## Cited sources
- <title> — <url>
- <title> — <url>

## Suggested correction
```text
<user's free-text suggestion>
```
```

The user's suggested correction is fenced — preserve only the fenced contents (strip the surrounding ```text … ``` lines). Extract each section verbatim.

If any section is missing, ask the user how to proceed (skip section, fill in manually, or abort) — don't silently invent content.

### 3. Show the user what we've got, before writing anything

Print the four extracted sections back so the user can sanity-check the parse. Then ask them:

- Whether to keep the user's suggestion **as-is** as the authoritative answer, or
- Whether they want to refine it first (in which case prompt them for the refined text right there in chat).

The authoritative answer is what gets retrieved by RAG, so this is the most important decision. Default to "refine" — the user's submission is rarely the final voice we want in the knowledge base.

### 4. Generate the filename

Slug rule: `<zero-padded-3-digit-issue-number>-<kebab-slug-of-title>.md`.

```
issue #5, title "Answer feedback: How do I install a FirstSpirit module?"
→ 005-how-do-i-install-a-firstspirit-module.md
```

Slug-of-title:
- Strip the `Answer feedback:` prefix if present.
- Lowercase, replace non-alphanumeric runs with `-`, trim leading/trailing `-`.
- Cap at 60 chars (truncate on a word boundary).

Target path: `deploy/sources/corrections/<slug>.md`. Create the directory if it doesn't exist (`mkdir -p deploy/sources/corrections`).

### 5. Write the correction file

Use this exact template. Substitute the placeholders; do not add other sections.

```markdown
---
source_issue: <N>
source_issue_url: <issue url>
created: <YYYY-MM-DD, today, ISO date>
---

# <Issue title, with the "Answer feedback: " prefix stripped>

## Question

<extracted ## User question, verbatim>

## Authoritative answer

<the refined answer the user just dictated, OR the original suggestion if they said keep-as-is>

## Sources

<extracted ## Cited sources bullet list, verbatim — keep the "title — url" formatting>

## Notes

<empty — leave a single blank line for the dev to add context later if they want>
```

After writing, show the user the file path and the final rendered contents.

### 6. Commit

Stage and commit only the new file. Don't touch anything else.

```bash
git add deploy/sources/corrections/<slug>.md
git commit -m "docs(corrections): from issue #<N>

<short one-line summary of the correction, e.g. the issue title>"
```

Push only if the user explicitly asks — corrections live on whatever branch the user is on, and they may want to bundle multiple before pushing.

### 7. Close the GH issue with a back-reference

```bash
gh issue close <N> --reason completed --comment "Addressed in correction file: deploy/sources/corrections/<slug>.md (commit <short-sha>)."
```

Use the short SHA of the commit we just made (`git rev-parse --short HEAD`).

### 8. Tell the user what's next

Wrap up with:
- The created file path + commit SHA.
- A reminder that the RAG only picks up the correction once the `corrections/` ingester is wired (currently a follow-up PR). If it IS wired and the app is running locally, suggest:
  ```bash
  curl -X POST http://127.0.0.1:8000/api/sources/sync \
    -H 'Content-Type: application/json' \
    -d '{"kind":"corrections"}'
  ```
  to re-sync immediately. If it's not wired, the file just sits ready for the next ingest pass.

## Refuse / fail-soft cases

- Non-numeric arg → ask the user for the issue number.
- Issue not found → relay `gh`'s error and stop.
- Issue lacks `feedback` label → stop with a clear explanation; the dev may process it by hand.
- Issue body missing one of the four expected sections → pause and ask the user how to proceed (skip / fill manually / abort).
- Filename slug collision (file already exists) → append `-2`, `-3`, … and tell the user a previous correction file existed.

## Scope discipline

This skill creates one file, makes one commit, and closes one issue. It does NOT:
- Edit the underlying source documents (vault notes / URL list).
- Tag source chunks as `legacy` or `superseded`.
- Modify the `corrections/` ingester or any backend code.
- Push or open PRs.

If the dev decides the right fix is to edit a source doc instead, abort this skill and do that by hand.
