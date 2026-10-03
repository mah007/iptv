---
name: dep-check
description: Check the latest stable version, release date, runtime support and licence of Python, npm, GitHub-released or runtime/server dependencies before pinning or adding them, and flag licences the Smart IPTV licence gate forbids (GPL/AGPL/SSPL/BUSL/non-commercial). Use when writing ADR-0001, adding or upgrading any dependency, choosing a Docker image tag, or answering "what's the latest version of X" / "can we use library Y".
argument-hint: "pypi:<name> npm:<name> gh:<owner>/<repo> eol:<product> ..."
---

# Dependency check

SPEC §1.2 and §1.8 require this before any pin: verify the latest stable version and its licence, and never import GPL/AGPL code.

## Run

```bash
python3 .claude/skills/dep-check/scripts/dep_check.py $ARGUMENTS
```

The four source prefixes:
- `pypi:<name>`: Python packages. The Notes column shows `requires_python`, so check it covers 3.13.
- `npm:<name>`: npm packages, including scoped names like `npm:@tanstack/react-router`. Notes show `engines.node`.
- `gh:<owner>/<repo>`: projects that ship as GitHub releases or Docker images, such as `gh:traefik/traefik` and `gh:meilisearch/meilisearch`. Anonymous calls are limited to 60 an hour, so set `GITHUB_TOKEN` or log in with `gh`.
- `eol:<product>`: runtimes and servers via endoflife.date, such as `eol:python`, `eol:nodejs`, `eol:django`, `eol:postgresql`, `eol:valkey`, `eol:redis`, `eol:nginx` and `eol:traefik`. It lists the support cycles that are still maintained, with their latest patch, LTS flag and end-of-life date.

The script exits 1 if anything is BLOCK.

## Read the verdict
| Gate | Meaning | Action |
|---|---|---|
| OK | Permissive (MIT, BSD, Apache-2.0, ISC, Zlib, MPL-2.0, PSF, …) | Pin it. |
| LGPL | Allowed only unmodified (e.g. guessit, psycopg) | Pin it; never vendor or patch it. Note this in the ADR. |
| BLOCK | GPL/AGPL/SSPL/BUSL/non-commercial | Don't import it. Find a permissive alternative or write our own (e.g. our own TMDB client instead of `tmdbsimple`). A tool may run only as a separate unmodified process or container. |
| CHECK | Licence unknown, mixed or unparsed | Open the repo's LICENSE files and decide by hand. Meilisearch shows CHECK because its repo mixes MIT (community edition) with an enterprise licence; we use MIT features only. |

## Choosing a version
- Prefer the newest **LTS or maintained** line that the rest of the stack supports, not simply the newest release. For example, Django LTS versus the latest feature release should be argued in the ADR.
- If the current line differs from SPEC §4 (for example, a newer Node LTS or PostgreSQL major), don't silently follow the spec or silently deviate from it. Record the choice and its reason in the ADR, and raise it at the checkpoint.
- Pin exact versions in lockfiles (`uv.lock`, `pnpm-lock.yaml`) and Docker image tags (digests in `compose.prod.yml`).
- Paste the script's table into the ADR as evidence, along with the date you ran it.
