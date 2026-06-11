# ♟ Chess Analyzer

A fully local chess game analyzer. Paste or upload a PGN, watch Stockfish evaluate every move in real time, then ask an LLM coach to explain any move in plain English — all running on your own machine, no cloud services required.

---

## Features

- **Live analysis** — Stockfish evaluates moves one by one with a streaming progress view, board updates, and move log as it runs
- **Move classification** — every move rated Best / Excellent / Good / Inaccuracy / Mistake / Blunder with centipawn loss
- **Interactive board** — step through positions with arrow keys or click any move; last-move highlighting and engine-best arrow shown
- **Animated eval bar** — white/black advantage bar updates as you navigate
- **LLM coach layer** — click "ask" on any move to get a plain-English explanation from a local Ollama model, streamed token by token
- **Per-player stats** — accuracy %, blunder/mistake/inaccuracy counts for both sides
- **No internet required** — everything runs locally: Stockfish, Ollama, and the web UI

---

## Requirements

### System dependencies

| Dependency | Version | Install |
|---|---|---|
| **Python** | ≥ 3.10 | [python.org](https://python.org) or `brew install python` |
| **Stockfish** | ≥ 16 (17+ recommended) | `brew install stockfish` |
| **Ollama** | ≥ 0.1.32 | [ollama.com](https://ollama.com) |

> **Apple Silicon note:** Stockfish and Ollama both have native Apple Silicon builds.
> Ollama uses the Neural Engine automatically on M1/M2/M3/M4 Macs.

### Python packages

| Package | Version | Purpose |
|---|---|---|
| `chess` | ≥ 1.10.0 | PGN parsing, engine interface, SVG board rendering |
| `fastapi` | ≥ 0.100.0 | Web framework (requires Pydantic v2) |
| `uvicorn[standard]` | ≥ 0.23.0 | ASGI server |
| `python-multipart` | ≥ 0.0.6 | Form data parsing |

> **Pydantic note:** FastAPI ≥ 0.100 requires **Pydantic v2**. If you have an older
> environment with Pydantic v1 you will see an import error on startup. Fix with:
> `pip install --upgrade "pydantic>=2.0" fastapi`

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/your-username/chess-analyzer.git
cd chess-analyzer
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate       # macOS / Linux
# .venv\Scripts\activate        # Windows
```

Or with conda:

```bash
conda create -n chess python=3.11
conda activate chess
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Stockfish

**macOS (Homebrew):**
```bash
brew install stockfish
which stockfish          # → /opt/homebrew/bin/stockfish
stockfish --version      # → Stockfish 17 or 18
```

**Linux:**
```bash
sudo apt install stockfish      # Ubuntu / Debian
sudo dnf install stockfish      # Fedora
```

**Manual install:** Download from [stockfishchess.org](https://stockfishchess.org/download/)
and point `STOCKFISH_PATH` at the binary.

### 5. Install Ollama and pull a model

```bash
# Install Ollama (macOS)
brew install ollama
# or download the app from https://ollama.com

# Start the Ollama service
ollama serve

# Pull the recommended model (in a new terminal)
ollama pull llama3.1:8b
```

**Model recommendations:**

| Model | Size | Quality | Speed |
|---|---|---|---|
| `llama3.1:8b` | 4.9 GB | ⭐⭐⭐⭐⭐ Best | Medium |
| `dolphin-llama3:8b` | 4.7 GB | ⭐⭐⭐⭐ | Medium |
| `dolphin-mistral:7b` | 4.1 GB | ⭐⭐⭐ | Fast |
| `llama3.2:3b` | 2.0 GB | ⭐⭐ | Very fast |

`llama3.1:8b` gives the most precise chess explanations. A 3B model works well
if you want faster responses or have limited RAM.

---

## Running

```bash
# Terminal 1 — Ollama (if not already running as a service)
ollama serve

# Terminal 2 — Chess Analyzer
uvicorn app:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## Usage

1. **Paste or drop a PGN** into the upload area. Any standard PGN works — exported
   from Lichess, Chess.com, a physical game you recorded, or the included `sample_game.pgn`.

2. **Set analysis depth** with the slider (default 18).
   Higher depth = more accurate but slower. Depth 20 is thorough, depth 15 is fast.

3. **Click Analyze Game.** The board updates live as Stockfish works through each move.
   A scrolling log shows classifications and evals in real time.

4. **Navigate the game** with arrow keys or the ⏮ ◀ ▶ ⏭ buttons:
   - `←` `→` — step one move
   - `Home` / `End` — jump to start or end position
   - Click any row in the move list to jump directly

5. **Ask the coach** by hovering a move row and clicking **ask**. A panel expands
   below with the LLM's explanation, streamed token by token. Use the model dropdown
   in the coach panel to switch between available Ollama models.

### CLI mode (no web UI)

```bash
python3 analyze.py sample_game.pgn
python3 analyze.py my_game.pgn --depth 20
python3 analyze.py my_game.pgn --stockfish /opt/homebrew/bin/stockfish
```

---

## Configuration

All configuration is via environment variables — no config file to edit.

| Variable | Default | Description |
|---|---|---|
| `STOCKFISH_PATH` | `stockfish` | Path to Stockfish binary |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API base URL |
| `OLLAMA_MODEL` | `llama3.1:8b` | Default model for commentary |
| `ALLOWED_ORIGINS` | `http://localhost:8000,http://127.0.0.1:8000` | Comma-separated CORS origins |

Example:

```bash
STOCKFISH_PATH=/opt/homebrew/bin/stockfish \
OLLAMA_MODEL=dolphin-mistral:7b \
uvicorn app:app --port 8000
```

---

## Project structure

```
chess-analyzer/
├── app.py              # FastAPI backend — analysis + LLM commentary
├── analyze.py          # CLI analyzer (standalone, no server needed)
├── requirements.txt
├── sample_game.pgn     # Test game (Immortal Game, 1851)
└── static/
    └── index.html      # Single-file web UI
```

---

## Move classification thresholds

| Classification | Centipawn loss | Symbol |
|---|---|---|
| Best | 0 | ✓ |
| Excellent | 1–10 | ! |
| Good | 11–25 | |
| Inaccuracy | 26–50 | ?! |
| Mistake | 51–100 | ? |
| Blunder | 101+ | ?? |

Thresholds match Lichess's classification system.

---

## Security notes

This tool is designed for **local use only**. The defaults are safe on your own machine.
If you ever expose it on a network, be aware of the following:

- **CORS** is restricted to `localhost` by default. Only widen `ALLOWED_ORIGINS` if
  you deliberately want network access.
- **PGN input** is capped at 128 KB and parsed in memory — no filesystem access,
  no path traversal possible.
- **Analysis depth** is clamped server-side to 1–28 regardless of what is submitted.
- **Stockfish** is launched via `popen_uci` (not a shell invocation) — the
  `STOCKFISH_PATH` value cannot be used for command injection.
- **LLM output** is HTML-escaped before rendering in the browser.
- **Do not expose this server to the public internet** without adding authentication.
  There is intentionally no rate limiting or multi-user isolation.

---

## Troubleshooting

**Stockfish not found**
```bash
which stockfish
# If empty, install it, then either add it to PATH or:
STOCKFISH_PATH=/full/path/to/stockfish uvicorn app:app --port 8000
```

**Pydantic / FastAPI import error on startup**
```bash
pip install --upgrade "pydantic>=2.0" "fastapi>=0.100" uvicorn python-multipart
```

**Ollama models not loading in dropdown**
```bash
# Verify Ollama is running and reachable
curl http://localhost:11434/api/tags
# Should return JSON. If it hangs or errors: ollama serve
```

**LLM commentary panel not appearing**
Open browser DevTools → Console (`Cmd+Option+J` on Mac) and look for:
```
[Chess Analyzer] /models response: ...
[Chess Analyzer] askCoach: <move> | model: <model>
```
If `/models` shows an error, Ollama is not reachable from the server process. Make
sure both `ollama serve` and `uvicorn` are running in the same network environment.

**Board shows blank squares**
The board is rendered as inline SVG — no external image CDN needed. If you see blank
squares, check the browser console for JavaScript errors and make sure `chess` (python-chess)
is installed correctly.

---

## Roadmap

- [ ] Opening book identification (ECO codes)
- [ ] Save and compare multiple games
- [ ] Export annotated PGN with engine comments
- [ ] Board orientation toggle
- [ ] Variation explorer — step through engine continuation lines

---

## License

MIT
