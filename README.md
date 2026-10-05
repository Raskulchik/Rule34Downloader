# Rule34Downloader

[Ru Version](https://github.com/Raskulchik/Rule34Downloader/blob/main/readmeru.md)

A small, polite tag downloader for rule34. Search by tags, save to a folder,
skip what you already have.

Originally a single 188-line `main.py`. Rewritten in v2 as a proper package.

## Why v2 exists

The v1 tool relied entirely on the old JSON `dapi` endpoint on `rule34.xxx`.
Upstream now answers that endpoint - and the whole site - with an anti-bot
interstitial, so v1 no longer downloaded anything:

```
$ curl -s -o /dev/null -w '%{http_code}' \
    'https://rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&tags=cute'
403
```

v2 therefore treats the listing backend as **pluggable** and ships a working
one. See [Sources](#sources).

## Install

Requires Python 3.11+.

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -e .
```

That gives you the `r34dl` command. The old `python main.py ...` invocation
still works too - `main.py` is now a thin launcher.

## Usage

Interactive, exactly like before:

```bash
r34dl
# Tags? (example: Miyabi -ai_generated): Miyabi -ai_generated
# How many posts? (0 or Enter = all): 20
# Start download of 20 posts? [y/N] y
```

Or fully scripted - no prompts, suitable for cron:

```bash
r34dl -t "Miyabi -ai_generated" -n 20 -y
```

### Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `-t`, `--tags` | prompt | Tag query. Prefix a tag with `-` to exclude it. |
| `-o`, `--output-dir` | `~/Downloads` | Base directory for downloads. |
| `-n`, `--limit` | all | Download at most N posts. |
| `-s`, `--source` | `auto` | `auto`, `paheal` or `json`. |
| `-c`, `--concurrency` | `5` | Parallel downloads (1-16). |
| `-r`, `--rate` | `4.0` | Max requests per second. |
| `--flat` | off | Save into the output dir directly, no tag subfolder. |
| `--overwrite` | off | Re-download files that already exist. |
| `-y`, `--yes` | off | Skip confirmation prompts. |
| `--dry-run` | off | Show what would be downloaded; write nothing. |
| `--cookie` | none | Cookie header, e.g. `'PHPSESSID=...'`. |
| `--max-pages` | `200` | Safety cap on listing pages. |
| `-v` / `-q` | - | Debug logging / quiet mode. |

Files land in `<output-dir>/<tags>/`, named after the post id with the
extension the source reported (`7451852.png`). If the target directory cannot
be created, the tool falls back to `~/Downloads/<tags>/` and says so.

## Sources

`--list-sources` prints what is available.

- **`paheal`** (default) - the rule34.paheal.net HTML listing. Needs no
  account. This is what makes the tool work again out of the box.
- **`json`** - the original `rule34.xxx` JSON API. Still implemented, but it is
  frequently behind a CAPTCHA wall. Pass `--cookie` with your own session, or
  just let `auto` pick `paheal`.
- **`auto`** - try `paheal` first, then fall back to `json`.

Adding a new backend means one module in `src/r34dl/sources/` implementing
`PostSource`; nothing else changes.

## Behaviour worth knowing

- **Resumable.** Existing files are skipped, so re-running after an interrupt
  continues where it stopped. Use `--overwrite` to force.
- **Atomic.** Downloads land in a temporary file and are renamed only on
  success, so an interrupted run never leaves a corrupt file that a later
  resume would skip.
- **Polite.** A shared token bucket caps request rate; a 429/503 slows every
  in-flight worker, not just the one that was answered. Defaults are
  deliberately gentle.
- **Interruptible.** `Ctrl+C` cancels cleanly and reports what completed.
- **Non-fatalling.** One failed file does not abort the rest. Failures are
  summarised at the end.

## Development

```bash
pip install -e '.[dev]'
pytest                 # 191 tests, no network access required
ruff check .
mypy                   # strict
```

Layout:

```
src/r34dl/
  cli.py          argument parsing, interactive + scripted modes
  pipeline.py     wires fetch -> resolve -> download
  sources/        pluggable listing backends
  net/            pooled HTTP, retries, backoff, rate limiting
  download.py     concurrency, resume, atomic writes
  storage.py      destination resolution, safe writes
  tagging.py      tag query parsing
  naming.py       filename sanitising, collision handling
  models.py       shared dataclasses
  ui.py           all console output and prompts
  errors.py       exception hierarchy
```

## A note

This tool only fetches what you explicitly search for. Keep the request rate
low (the default is conservative), respect the sites' terms, and do not
redistribute content you do not have the right to. That is all.

## License

MIT - see [LICENSE](LICENSE).