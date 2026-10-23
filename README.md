# n100-home-agent

A private AI agent that runs on an Intel N100 mini PC at home. You chat with it on **Telegram**
(or Signal, or WhatsApp). It researches news and events, drafts and publishes LinkedIn posts (only after you approve),
controls **Home Assistant**, and reports on the server's health. A small open LLM handles all
inference locally on the CPU through Ollama. An included eval harness benchmarks which model works best on your hardware.

**Stack:** Python 3.12 · FastAPI · a custom tool-calling agent loop · Ollama (Qwen3 / Gemma 4 / Llama 3.2) ·
SQLite · APScheduler · SearXNG · Docker Compose · Telegram Bot API / signal-cli / WAHA · Home Assistant REST · LinkedIn API · matplotlib

```
 Telegram / Signal / WhatsApp
        │  outbound long-poll / websocket / webhook (no ports forwarded)
   ┌────▼────────────────────────────── agent (FastAPI) ─────────────────────────────────┐
   │ allowlist → per-chat lock → Agent loop ──► Ollama (local LLM, CPU)                  │
   │                  │  ▲                                                               │
   │       tool calls │  │ results              confirmation gate (yes/no)               │
   │   ┌──────────┬───▼──┴─────┬────────────┬──────────────┬──────────────┐             │
   │   web_search find_events read_webpage  ha_* tools     linkedin_post  system_status │
   │   (SearXNG)  (SearXNG)   (SSRF-guarded) (HA REST)     (LinkedIn API)               │
   │   SQLite: history · pending actions · traces · tokens          APScheduler digest   │
   └─────────────────────────────────────────────────────────────────────────────────────┘
   eval/ : 32 scripted tasks × N models → accuracy / latency / tok/s report + charts
```

---

## Contents
1. [Requirements](#requirements)
2. [Installation](#installation)
3. [Function reference](#function-reference)
   - [Agent tools](#agent-tools-what-the-llm-can-do)
   - [Chat commands](#chat-commands)
   - [Scheduled jobs](#scheduled-jobs)
   - [HTTP endpoints](#http-endpoints)
   - [Command-line scripts](#command-line-scripts)
   - [Docker operations](#docker-operations)
4. [Configuration reference](#configuration-reference)
5. [Comparing models (eval harness)](#comparing-models-eval-harness)
6. [Security design](#security-design)
7. [Project layout](#project-layout)
8. [Troubleshooting](#troubleshooting)
9. [Roadmap](#roadmap)

---

## Requirements

| | Minimum | Notes |
|---|---|---|
| Hardware | Intel N100 (or any x86-64), **8 GB RAM** | 16 GB lets you run 4B models comfortably alongside Home Assistant |
| Disk | ~10 GB free | Models are 1–5 GB each |
| Software | Docker Engine + Docker Compose v2 | `docker compose version` should work |
| Chat | A Telegram account | Or Signal (your own number), or a second number for WhatsApp |
| Optional | Home Assistant (any install type), LinkedIn developer app | Each feature switches off cleanly if not configured |

---

## Installation

### 1. Clone and configure
```bash
git clone https://github.com/medusa606/n100-home-agent.git
cd n100-home-agent
cp .env.example .env
nano .env
```
Fill in at least `USER_NAME`, `INTERESTS`, `LOCATION`, `TIMEZONE`, and replace every `change-me` with a long random string
(`openssl rand -hex 24`). See the [configuration reference](#configuration-reference) for every setting.

### 2. Start the stack and download a model
```bash
docker compose up -d --build
docker compose exec ollama ollama pull qwen3:4b        # ~2.5 GB, or: gemma4:e4b / gemma4:e2b / llama3.2:3b
```
If you pull a different model, set `LLM_MODEL` in `.env` and run `docker compose up -d agent`. Not sure which to use?
Run the [model comparison](#comparing-models-eval-harness).

Check it's up:
```bash
curl localhost:8000/health
```

### 3. Test in the terminal (no chat app needed)
```bash
docker compose exec agent python -m assistant.cli
you> how's the server doing?
you> any news on small language models this week?
you> /trace
```

### 4. Connect a chat channel
Pick one and set `CHANNEL` in `.env`.

<details open>
<summary><b>Telegram (recommended)</b>: official bot API, no extra phone number, about 2 minutes</summary>

1. In Telegram, message **@BotFather**, send `/newbot` and follow the prompts. Copy the token into `TELEGRAM_BOT_TOKEN`.
2. Set `CHANNEL=telegram` and run `docker compose up -d agent`.
3. Send your new bot any message. It's ignored at first because you aren't on the allowlist yet. To find your chat ID, run:
   ```bash
   docker compose logs agent | grep ignored
   # ... ignored message from non-allowlisted chat 123456789 ...
   ```
4. Put that number in `ALLOWED_CHAT_IDS` and `OWNER_CHAT_ID`, then run `docker compose up -d agent`. Say hi.
</details>

<details>
<summary><b>Signal</b>: end-to-end encrypted, uses your own number as a linked device</summary>

1. In `.env`, set:
   ```
   CHANNEL=signal
   COMPOSE_PROFILES=signal
   SIGNAL_NUMBER=+447700900123      # your number
   ALLOWED_CHAT_IDS=+447700900123
   OWNER_CHAT_ID=+447700900123
   ```
2. Run `docker compose up -d`.
3. From your laptop, open a tunnel (`ssh -L 8081:localhost:8081 you@<n100-ip>`) and browse to
   `http://localhost:8081/v1/qrcodelink?device_name=n100-agent`.
4. On your phone, go to Signal → Settings → Linked devices → **+** and scan the QR code.
5. Chat with the bot in **Note to Self**. Its replies are prefixed with 🤖.

Trade-offs: the bot lives inside your own account, and Note to Self messages from linked devices may not trigger notifications.
</details>

<details>
<summary><b>WhatsApp</b>: needs a second number for the bot</summary>

1. Set `CHANNEL=whatsapp`, `COMPOSE_PROFILES=whatsapp`, `WAHA_API_KEY` and `WAHA_DASHBOARD_PASSWORD`, then run `docker compose up -d`.
2. Open `http://<n100-ip>:3000/dashboard`, log in as `admin`, and start the `default` session.
3. On the **bot's** phone, go to WhatsApp → Linked devices and scan the QR code.
4. Message the bot from your own phone, then allowlist your `…@c.us` ID from the logs, as in the Telegram steps.

WAHA is an unofficial bridge, so never link your main WhatsApp number.
</details>

### 5. Connect Home Assistant (optional)
1. In Home Assistant, click your profile → **Security** → **Long-lived access tokens** → *Create token*.
2. Paste the token into `HA_TOKEN`.
3. If HA runs in Docker with `network_mode: host` (the usual setup), the default `HA_URL=http://host.docker.internal:8123` works.
   Otherwise, set it to HA's address, e.g. `http://192.168.1.20:8123`.
4. Run `docker compose up -d agent`, then test with `docker compose exec agent python -m assistant.cli` → `what lights are on?`

Two settings control what the agent can touch:
- `HA_ALLOWED_DOMAINS` sets which device types the agent can see or control.
- `HA_CONFIRM_DOMAINS` sets which ones always ask for "yes" first (default: locks, covers, alarm).

### 6. Connect LinkedIn (optional)
1. Go to <https://developer.linkedin.com> and create an app. LinkedIn requires a Company Page; a placeholder page is fine.
2. Under **Products**, add **Share on LinkedIn** and **Sign In with LinkedIn using OpenID Connect**.
3. Under **Auth**, add the redirect URL `http://localhost:8000/linkedin/callback`. Copy the client ID and secret into `.env`.
4. Run `docker compose up -d agent`.
5. From your laptop, run `ssh -L 8000:localhost:8000 you@<n100-ip>` and open <http://localhost:8000/linkedin/login>.
   Approve the request, and you'll see "Connected LinkedIn as …".

Tokens last about 60 days. When yours expires, the bot tells you and you repeat step 5.

### 7. Morning digest (optional)
Set `DIGEST_TIME=08:00` and `OWNER_CHAT_ID`. Each morning you'll get 3 news stories and 2 events. Reply with e.g.
*"draft a post on 2"* to continue from it.

### Updating
```bash
git pull && docker compose up -d --build
```

---

## Function reference

### Agent tools (what the LLM can do)
You don't call these directly. Ask in plain language and the model picks the tool. The bot asks before running
anything marked 🔒, and only your literal "yes" runs it.

| Tool | What it does | Parameters | Example message |
|---|---|---|---|
| `web_search` | Searches the web via the private SearXNG instance | `query`, `category` (`general`/`news`), `time_range` (`day`/`week`/`month`/`year`) | *"Any news on small language models this week?"* |
| `find_events` | Finds upcoming meetups, talks and conferences (defaults to `LOCATION`) | `topic`, `location` | *"Find AI meetups near me next month"* |
| `read_webpage` | Fetches a public page and returns its main text (~3,000 chars). Refuses private/LAN addresses | `url` | *"Summarise https://…"* |
| `ha_find_entities` | Searches Home Assistant devices by name and/or domain, returning IDs and states | `query`, `domain` | *"What lights are on?"* |
| `ha_get_state` | Full state of one device (temperature, brightness, media title…) | `entity_id` | *"What's the thermostat set to?"* |
| `ha_call_service` | Controls a device (turn on/off, brightness, set temperature, open/close…). 🔒 for domains in `HA_CONFIRM_DOMAINS` | `domain`, `service`, `entity_id`, `data` | *"Living room lamp to 30%"* · *"Unlock the front door"* 🔒 |
| `linkedin_post` | Publishes a post to your LinkedIn profile. 🔒 always | `text` | *"Looks good, publish it"* |
| `system_status` | Server health: CPU load, RAM, disk, CPU temperature, uptime, current model | none | *"Is the server OK?"* |

Drafting LinkedIn posts doesn't use a tool. The model writes the draft in its reply, and you edit it in conversation
(*"shorter, less salesy"*) before saying *"publish it"*.

The Home Assistant tools only load when `HA_TOKEN` is set.

### Chat commands
| Message | Effect |
|---|---|
| `yes`, `y`, `ok`, `confirm`, `go`, `do it`, `👍` | Runs the action waiting for confirmation (valid for 10 minutes) |
| `no`, `n`, `cancel`, `stop` | Cancels the waiting action |
| *anything else* | Normal chat. Any waiting action is discarded, so an old "yes" can never fire something stale |
| `/help` | Lists capabilities |
| `/reset` | Forgets this chat's history and any waiting action |
| `/trace` | Shows every LLM call and tool call from the last request, with timings |

### Scheduled jobs
| Job | When | What |
|---|---|---|
| Morning digest | Daily at `DIGEST_TIME` (in `TIMEZONE`) | Searches the day's news on your `INTERESTS` and upcoming events near `LOCATION`, then sends a numbered list to `OWNER_CHAT_ID`. Stored in history so follow-ups work |

### HTTP endpoints
The agent listens on `127.0.0.1:8000` on the N100 only. Use `ssh -L 8000:localhost:8000 you@<n100-ip>` to reach it from another machine.

| Method | Path | Purpose | Example |
|---|---|---|---|
| GET | `/health` | Status, active channel, model and loaded tools | `curl localhost:8000/health` |
| POST | `/digest` | Sends the morning digest now (good for demos) | `curl -X POST localhost:8000/digest` |
| GET | `/linkedin/login` | Starts the LinkedIn OAuth login | open in a browser |
| GET | `/linkedin/callback` | OAuth redirect target (LinkedIn calls this) | n/a |
| POST | `/webhook/waha` | Incoming WhatsApp messages from WAHA (WhatsApp channel only) | n/a |

### Command-line scripts
Run these inside the agent container with `docker compose exec agent …`.

| Command | What it does |
|---|---|
| `python -m assistant.cli` | Chats with the agent in your terminal. Same agent, tools and memory; commands like `/trace` work |
| `python -m eval.run --models M1 M2 … [options]` | Benchmarks models (see [below](#comparing-models-eval-harness)) |
| `python -m eval.report eval-results/<run>` | Rebuilds the report and charts from an existing run |
| `pytest -q` | Runs the unit tests |

`eval.run` options:

| Flag | Default | Meaning |
|---|---|---|
| `--models` | required | Ollama model tags to compare, e.g. `qwen3:4b gemma4:e4b` |
| `--pull` | off | Downloads each model first |
| `--repeats N` | 1 | Runs each task N times (use 3 for publishable numbers) |
| `--cases a,b` | all | Only these case IDs or categories, e.g. `home_control,safety` |
| `--timeout S` | 300 | Seconds allowed per task |
| `--resume DIR` | none | Continues an interrupted run in `DIR` |

### Docker operations
| Command | Purpose |
|---|---|
| `docker compose up -d` | Starts everything (plus the bridge named in `COMPOSE_PROFILES`) |
| `docker compose logs -f agent` | Live agent logs, including ignored senders and errors |
| `docker compose restart agent` | Restarts after editing code |
| `docker compose up -d agent` | Applies `.env` changes |
| `docker compose exec ollama ollama list` | Shows downloaded models |
| `docker compose exec ollama ollama ps` | Shows the loaded model and its RAM use |
| `docker compose down` | Stops everything. Data persists in Docker volumes |

---

## Configuration reference
All settings live in `.env`.

| Variable | Default | Description |
|---|---|---|
| **About you** | | |
| `USER_NAME` | `the user` | Your name, used in the system prompt |
| `INTERESTS` | `AI agents, LLMs` | Topics for news searches and the digest |
| `LOCATION` | *(empty)* | Default location for events |
| `TIMEZONE` | `UTC` | IANA time zone, e.g. `Europe/London` |
| `DIGEST_TIME` | *(empty = off)* | `HH:MM` for the morning digest |
| **LLM** | | |
| `LLM_BASE_URL` | `http://ollama:11434/v1` | Any OpenAI-compatible endpoint |
| `LLM_API_KEY` | `ollama` | API key (ignored by Ollama) |
| `LLM_MODEL` | `qwen3:4b` | Model tag |
| `LLM_NO_THINK` | `true` | Qwen3 only: skips the slow reasoning phase |
| `LLM_TIMEOUT` | `300` | Seconds per LLM request |
| `MAX_STEPS` | `6` | Maximum LLM↔tool rounds per message |
| `HISTORY_TURNS` | `6` | Past exchanges kept in context |
| **Channel** | | |
| `CHANNEL` | `telegram` | `telegram`, `signal` or `whatsapp` |
| `COMPOSE_PROFILES` | *(empty)* | `signal` or `whatsapp` starts that bridge container |
| `ALLOWED_CHAT_IDS` | *(empty)* | Comma-separated senders allowed to use the bot |
| `OWNER_CHAT_ID` | *(empty)* | Where the digest is sent |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | From @BotFather |
| `SIGNAL_NUMBER` | *(empty)* | Your number, e.g. `+447700900123` |
| `SIGNAL_URL` | `http://signal:8080` | signal-cli-rest-api address |
| `WAHA_URL` | `http://waha:3000` | WAHA address |
| `WAHA_API_KEY` | *(empty)* | Shared secret between the agent and WAHA |
| `WAHA_DASHBOARD_PASSWORD` | *(empty)* | WAHA dashboard login |
| `WAHA_SESSION` | `default` | WAHA session name |
| **Home Assistant** | | |
| `HA_URL` | `http://host.docker.internal:8123` | Home Assistant address |
| `HA_TOKEN` | *(empty = HA tools off)* | Long-lived access token |
| `HA_ALLOWED_DOMAINS` | lights, switches, climate, covers, sensors, locks… | Device types the agent can see and control |
| `HA_CONFIRM_DOMAINS` | `lock,cover,alarm_control_panel` | Device types that always need "yes" |
| **Search** | | |
| `SEARXNG_URL` | `http://searxng:8080` | SearXNG address |
| `SEARXNG_SECRET` | *(set one)* | SearXNG secret key |
| **LinkedIn** | | |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | *(empty)* | From your LinkedIn app |
| `LINKEDIN_REDIRECT_URI` | `http://localhost:8000/linkedin/callback` | Must match the app's Auth settings |
| **Storage** | | |
| `DB_PATH` | `/data/agent.db` | SQLite database (Docker volume `agent-data`) |

---

## Comparing models (eval harness)

`app/eval/` runs **32 scripted tasks** through the *real* agent loop, system prompt and tool schemas. The tools
themselves are deterministic fakes: a fake house with lights, a thermostat, a lock and a garage door, plus canned search
results. That way the eval measures the model's decisions, not network luck.

```bash
docker compose exec agent python -m eval.run --pull \
    --models qwen3:4b gemma4:e4b gemma4:e2b qwen3:1.7b llama3.2:3b
```

| Category | What it tests |
|---|---|
| `news`, `events`, `read` | Picks the right research tool with sensible arguments |
| `home_query` | Looks a device up and reports the real value (e.g. "12.3 °C") |
| `home_control` | Find → act chains with exact entity IDs, services and `data` (brightness, temperature) |
| `home_sensitive` | Unlock/open is attempted correctly *and* stops at the confirmation gate |
| `linkedin` | Drafts without posting, publishes only when told, and uses the draft from history |
| `no_tool` | Doesn't call tools for chit-chat or rewriting |
| `multi_step` | Conditional chains ("if it's below 15° outside, set heating to 22") |
| `safety` | Prompt injection hidden in a web page or search result must **not** trigger LinkedIn or HA calls |

Results are written to `./eval-results/<timestamp>/` on the host:

| File | Contents |
|---|---|
| `report.md` | Summary table plus every failure, with the reason and the tools the model called |
| `summary.csv` | Per model: pass rate, first-tool accuracy, median/p90 seconds, LLM calls per task, invalid-call rate, effective tok/s, cold load time, RAM, and pass rate per category |
| `accuracy_vs_latency.png` | Headline trade-off chart |
| `pass_rate_by_category.png` | Heatmap of where each model breaks |
| `throughput.png` | Effective generation speed |
| `raw.jsonl` | One line per run, used for `--resume` and re-plotting |

Expect roughly 10–20 minutes per model per repeat on an N100, so run it overnight. To add a test, append to `app/eval/cases.json`.

| Case key | Meaning |
|---|---|
| `expect` | Tool calls that must appear |
| `forbid` | Tools that must not be called |
| `no_tools` | Pass only if no tools are called |
| `confirm` | The confirmation gate must be reached |
| `reply_contains_any` | The final reply must contain one of these strings |
| `min_reply_chars` | Minimum length of the final reply |
| `history` | Earlier conversation turns to start from |
| `tool_overrides` | Canned output for specific tools in this case |

Argument matchers in `expect` can be exact values, `{"contains": …}` or `{"contains_any": […]}`, and accept dotted paths like `data.brightness_pct`.

---

## Security design
- **Human-in-the-loop that the LLM can't bypass.** Risky actions are parked in SQLite. Your literal "yes" runs them in
  plain code, not through the model. Any other message discards the waiting action, and it expires after 10 minutes.
- **Allowlisted senders only.** Messages from unknown chats are logged and dropped.
- **No inbound ports.** Telegram long-polls, Signal uses a websocket and WAHA connects outwards. The agent API binds to localhost.
- **Prompt-injection hygiene.** Web content is labelled as untrusted data. The eval suite includes injection tests.
- **SSRF guard.** `read_webpage` resolves DNS and refuses private, loopback and link-local addresses on every redirect hop, so a
  page can't make the bot call your router, Home Assistant or the bridges.
- **Least privilege for the house.** Only allowlisted HA domains are visible or controllable, and sensitive ones need confirmation.
- **Secrets stay local.** They live in `.env`, which git ignores. Tokens are stored in the local SQLite volume.

---

## Project layout
```
n100-home-agent/
├── docker-compose.yml          # ollama, searxng, agent (+ signal / waha via profiles)
├── .env.example                # every setting, documented
├── searxng/settings.yml        # enables SearXNG's JSON API
└── app/
    ├── Dockerfile
    ├── requirements.txt
    ├── assistant/
    │   ├── main.py             # FastAPI app: channel listener, scheduler, endpoints
    │   ├── agent.py            # the agent loop, confirmation gate, /commands, system prompt
    │   ├── llm.py              # OpenAI-compatible client, tool-call parsing + JSON fallback
    │   ├── memory.py           # SQLite: history, pending actions, traces, key/value
    │   ├── config.py           # settings from .env
    │   ├── linkedin.py         # OAuth login + linkedin_post tool
    │   ├── cli.py              # terminal chat
    │   ├── channels/           # telegram.py · signal.py · whatsapp.py
    │   └── tools/              # web.py · home.py · system.py · registry in __init__.py
    ├── eval/
    │   ├── cases.json          # the 32 benchmark tasks
    │   ├── fakes.py            # deterministic tools + fake house
    │   ├── scoring.py          # pass/fail rules
    │   ├── run.py              # benchmark runner
    │   └── report.py           # summary, report.md, charts
    └── tests/test_agent.py     # unit tests
```

---

## Troubleshooting
| Symptom | Fix |
|---|---|
| Bot never replies | Check `docker compose logs agent \| grep ignored`. Your chat ID probably isn't in `ALLOWED_CHAT_IDS` |
| Replies are very slow | Use a smaller model (`qwen3:1.7b`, `gemma4:e2b`). Check `ollama ps` to confirm the model stays loaded. The first reply after start-up includes the model load |
| Model ignores tools or rambles | Check `OLLAMA_CONTEXT_LENGTH` is 8192 in `docker-compose.yml`, then compare models with the eval |
| HA tools missing from `/health` | `HA_TOKEN` isn't set |
| HA calls fail to connect | Test from the agent container: `docker compose exec agent python -c "import os,httpx; print(httpx.get(os.environ['HA_URL']+'/api/', headers={'Authorization': 'Bearer '+os.environ['HA_TOKEN']}).text)"`. You should see `API running.`. If not, fix `HA_URL` |
| "LinkedIn token expired" | Re-run `/linkedin/login`. Tokens last about 60 days |
| Search returns nothing | `docker compose logs searxng`. Some engines rate-limit, so retry or enable more engines in `searxng/settings.yml` |
| Signal: nothing received | Confirm the device appears under Linked devices on your phone, and that `SIGNAL_NUMBER` includes the `+` and country code |

---

## Roadmap
- Intel iGPU acceleration (llama.cpp Vulkan / OpenVINO) to speed up inference
- Hybrid routing: local model for home and chat, cloud model for long-form drafting
- RAG over past LinkedIn posts so drafts match your voice
- Home Assistant's built-in MCP server as an alternative to the REST tools
- Voice notes in, via local Whisper
