#!/usr/bin/env python3
"""
Chess Analyzer — Local chess game analyzer using Stockfish + Ollama.

Usage:
    uvicorn app:app --reload --port 8000

Environment variables:
    STOCKFISH_PATH   Path to Stockfish binary (default: "stockfish")
    OLLAMA_URL       Ollama base URL (default: "http://localhost:11434")
    OLLAMA_MODEL     Default model name (default: "llama3.1:8b")
    ALLOWED_ORIGINS  Comma-separated CORS origins (default: "http://localhost:8000")
"""

import io
import json
import os
import asyncio
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, AsyncGenerator

import chess
import chess.engine
import chess.pgn
import chess.svg

from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ── Config ────────────────────────────────────────────────────────────────────

STOCKFISH_PATH  = os.environ.get("STOCKFISH_PATH",  "stockfish")
OLLAMA_URL      = os.environ.get("OLLAMA_URL",      "http://localhost:11434")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL",    "llama3.1:8b")

_raw_origins    = os.environ.get("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# Hard limits — prevent runaway CPU/memory from malicious or accidental inputs
MAX_PGN_BYTES   = 128 * 1024    # 128 KB — more than any real game
MIN_DEPTH       = 1
MAX_DEPTH       = 28            # depth 28 already takes minutes on fast hardware

# Preferred model ranking for auto-selection
_MODEL_PREFERENCE = [
    "llama3.1:8b",
    "dolphin-llama3:8b",
    "dolphin-mistral:7b",
    "huihui_ai/llama3.2-abliterate:3b",
    "dolphin-phi:latest",
]

DEFAULT_DEPTH = 18

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Chess Analyzer", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def score_to_cp_white(score: chess.engine.Score) -> Optional[float]:
    """Return centipawn eval from White's POV. ±10000 for mate lines."""
    if score.is_mate():
        return 10000 if score.mate() > 0 else -10000
    return score.score()


def classify(cp_loss: float) -> str:
    if cp_loss <= 0:   return "best"
    if cp_loss <= 10:  return "excellent"
    if cp_loss <= 25:  return "good"
    if cp_loss <= 50:  return "inaccuracy"
    if cp_loss <= 100: return "mistake"
    return "blunder"


SYMBOLS = {
    "best": "✓", "excellent": "!", "good": "",
    "inaccuracy": "?!", "mistake": "?", "blunder": "??",
}


def board_svg(board: chess.Board,
              last_move: Optional[chess.Move] = None,
              best_move: Optional[chess.Move] = None) -> str:
    arrows = []
    if best_move:
        arrows.append(chess.svg.Arrow(
            best_move.from_square, best_move.to_square, color="#c9a84c88"
        ))
    return chess.svg.board(
        board,
        lastmove=last_move,
        arrows=arrows,
        size=360,
        colors={
            "square light":          "#f0d9b5",
            "square dark":           "#b58863",
            "square light lastmove": "#cdd26a",
            "square dark lastmove":  "#aaa23a",
        },
    )


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

# ── Streaming analysis ────────────────────────────────────────────────────────

async def analyze_stream(pgn_text: str, depth: int) -> AsyncGenerator[str, None]:
    # Parse PGN — uses StringIO so no filesystem access
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        yield sse("error", {"message": "Could not parse PGN — check your notation."})
        return

    headers   = dict(game.headers)
    all_moves = list(game.mainline_moves())
    total     = len(all_moves)

    if total == 0:
        yield sse("error", {"message": "No moves found in PGN."})
        return

    yield sse("meta", {
        "white":     headers.get("White",  "White"),
        "black":     headers.get("Black",  "Black"),
        "result":    headers.get("Result", "*"),
        "event":     headers.get("Event",  "?"),
        "date":      headers.get("Date",   "?"),
        "total":     total,
        "depth":     depth,
        "start_svg": board_svg(chess.Board()),
    })
    await asyncio.sleep(0)

    # Open Stockfish — popen_uci never invokes a shell, so STOCKFISH_PATH
    # cannot be used for command injection even if tampered with via env var.
    engine = None
    try:
        engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    except FileNotFoundError:
        yield sse("error", {
            "message": (
                f"Stockfish not found at '{STOCKFISH_PATH}'. "
                "Install via: brew install stockfish  "
                "or set STOCKFISH_PATH to the binary location."
            )
        })
        return

    board           = game.board()
    white_cp_losses = []
    black_cp_losses = []

    try:
        for i, move in enumerate(all_moves):
            is_white    = (i % 2 == 0)
            move_number = (i // 2) + 1

            info_before = engine.analyse(
                board, chess.engine.Limit(depth=depth), multipv=2
            )
            best_info  = info_before[0] if isinstance(info_before, list) else info_before
            cp_before  = score_to_cp_white(best_info["score"].white())
            best_uci   = str(best_info["pv"][0]) if best_info.get("pv") else None
            best_move_obj = chess.Move.from_uci(best_uci) if best_uci else None
            best_san   = board.san(best_move_obj) if best_move_obj else "?"
            played_san = board.san(move)

            board.push(move)
            svg_after = board_svg(board, last_move=move)

            info_after = engine.analyse(board, chess.engine.Limit(depth=depth))
            info_after = info_after[0] if isinstance(info_after, list) else info_after
            cp_after   = score_to_cp_white(info_after["score"].white())

            if cp_before is not None and cp_after is not None:
                cp_loss = (cp_before - cp_after) if is_white else (cp_after - cp_before)
                cp_loss = max(0.0, cp_loss)
            else:
                cp_loss = 0.0

            classification = classify(cp_loss)
            (white_cp_losses if is_white else black_cp_losses).append(cp_loss)

            def fmt(cp):
                if cp is None: return None
                if abs(cp) >= 9000: return 10000 if cp > 0 else -10000
                return round(cp / 100, 2)

            yield sse("move", {
                "index":          i,
                "move_number":    move_number,
                "is_white":       is_white,
                "san":            played_san,
                "best_san":       best_san,
                "uci":            move.uci(),
                "cp_before":      fmt(cp_before),
                "cp_after":       fmt(cp_after),
                "cp_loss":        round(cp_loss / 100, 2),
                "classification": classification,
                "symbol":         SYMBOLS.get(classification, ""),
                "svg_after":      svg_after,
                "progress":       i + 1,
                "total":          total,
            })
            await asyncio.sleep(0)

    finally:
        # Always shut down the engine process, even if an exception occurred
        engine.quit()

    def stats(losses):
        if not losses:
            return {"accuracy": 100.0, "blunders": 0, "mistakes": 0, "inaccuracies": 0}
        avg = sum(losses) / len(losses)
        return {
            "accuracy":     round(max(0.0, min(100.0, 100.0 - avg / 10)), 1),
            "blunders":     sum(1 for x in losses if x > 300),
            "mistakes":     sum(1 for x in losses if 100 < x <= 300),
            "inaccuracies": sum(1 for x in losses if 50  < x <= 100),
        }

    yield sse("done", {
        "white_stats": stats(white_cp_losses),
        "black_stats": stats(black_cp_losses),
    })

# ── LLM prompt ────────────────────────────────────────────────────────────────

def _build_prompt(move: dict, game_meta: dict) -> str:
    """Chess-coach prompt tuned for Llama 3.1 8B instruction style."""
    player    = "White" if move["is_white"] else "Black"
    cp_loss   = float(move.get("cp_loss", 0))
    cls       = move["classification"]
    played    = move["san"]
    best      = move["best_san"]
    cp_after  = move.get("cp_after")
    move_num  = int(move["move_number"])
    white     = game_meta.get("white", "White")[:40]   # truncate to prevent prompt injection
    black     = game_meta.get("black", "Black")[:40]
    same_move = played == best

    if cp_after is None:
        eval_desc = "unclear"
    elif abs(cp_after) >= 100:
        eval_desc = "White is winning" if cp_after > 0 else "Black is winning"
    elif cp_after > 2:
        eval_desc = f"White is better ({cp_after:+.2f})"
    elif cp_after < -2:
        eval_desc = f"Black is better ({cp_after:+.2f})"
    else:
        eval_desc = f"roughly equal ({cp_after:+.2f})"

    loss_desc = (
        "no centipawn loss" if cp_loss < 0.1
        else f"minor loss ({cp_loss:.2f} pawns)" if cp_loss < 0.5
        else f"{cp_loss:.2f} pawn loss"
    )

    if same_move:
        task = (
            "This was the engine's top choice. Explain in 2–3 sentences why it is "
            "a strong move — what threat, structure, or idea it creates."
        )
    elif cls in ("blunder", "mistake"):
        task = (
            f"This was a {cls} ({loss_desc}). In 3–4 sentences: explain what the "
            f"player likely intended with {played}, describe concretely what goes "
            f"wrong tactically or strategically, then explain what {best} achieves "
            "instead and why it is clearly stronger."
        )
    elif cls == "inaccuracy":
        task = (
            f"This was an inaccuracy ({loss_desc}). In 2–3 sentences: explain what "
            f"{played} tries to do and what subtle problem it creates, then explain "
            f"why {best} is the more accurate choice."
        )
    else:
        task = (
            f"This was a {cls} move. In 2–3 sentences: explain what {played} "
            "accomplishes and why it is a good decision here."
        )

    return (
        "You are a direct, knowledgeable chess coach. "
        "Give practical feedback on one move. No preamble, no bullet points, no heading.\n\n"
        f"GAME: {white} (White) vs {black} (Black), move {move_num}\n"
        f"PLAYER: {player}\n"
        f"MOVE PLAYED: {played}\n"
        f"ENGINE BEST: {best}\n"
        f"CLASSIFICATION: {cls}\n"
        f"POSITION AFTER MOVE: {eval_desc}\n\n"
        f"{task}"
    )

# ── Ollama streaming ──────────────────────────────────────────────────────────

async def _stream_ollama(model: str, prompt: str) -> AsyncGenerator[str, None]:
    """Stream tokens from Ollama /api/generate as SSE."""
    payload = json.dumps({
        "model":  model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature":    0.25,
            "num_predict":    350,
            "top_p":          0.9,
            "repeat_penalty": 1.1,
        },
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            for raw_line in resp:
                line = raw_line.decode().strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = chunk.get("response", "")
                if token:
                    yield sse("token", {"text": token})
                if chunk.get("done"):
                    yield sse("done", {})
                    return
    except urllib.error.URLError as e:
        yield sse("error", {"message": f"Ollama unreachable: {e.reason}. Is Ollama running?"})
    except Exception as e:
        yield sse("error", {"message": f"Unexpected error: {type(e).__name__}"})

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def serve_ui():
    return FileResponse(str(Path(__file__).parent / "static" / "index.html"))


@app.post("/analyze/stream")
async def analyze_endpoint(
    pgn:   str = Form(...),
    depth: int = Form(DEFAULT_DEPTH),
):
    # Validate inputs before any processing
    if len(pgn.encode()) > MAX_PGN_BYTES:
        raise HTTPException(status_code=413, detail="PGN too large (max 128 KB).")
    depth = max(MIN_DEPTH, min(MAX_DEPTH, depth))  # clamp silently

    return StreamingResponse(
        analyze_stream(pgn, depth),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/models")
async def list_models():
    """Return available Ollama models sorted by preference."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        available = [m["name"] for m in data.get("models", [])]
        preferred = [m for m in _MODEL_PREFERENCE if m in available]
        others    = sorted(m for m in available if m not in _MODEL_PREFERENCE)
        ordered   = preferred + others

        if OLLAMA_MODEL in available:
            default = OLLAMA_MODEL
        elif preferred:
            default = preferred[0]
        elif available:
            default = available[0]
        else:
            default = ""

        return {"models": ordered, "current": default}
    except Exception as e:
        return {"models": [], "current": "", "error": str(e)}


@app.post("/commentary")
async def commentary_endpoint(
    move_data: str = Form(...),
    game_meta: str = Form(...),
    model:     str = Form(""),
):
    # Validate move_data and game_meta are well-formed JSON objects
    try:
        move = json.loads(move_data)
        meta = json.loads(game_meta)
        if not isinstance(move, dict) or not isinstance(meta, dict):
            raise ValueError("Expected JSON objects")
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid move_data or game_meta.")

    # Resolve model — user choice → env default → first available
    chosen_model = model.strip() or OLLAMA_MODEL
    if not chosen_model:
        try:
            req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            chosen_model = models[0] if models else ""
        except Exception:
            pass

    if not chosen_model:
        async def _no_model():
            yield sse("error", {"message": "No Ollama model found. Run: ollama pull llama3.1:8b"})
        return StreamingResponse(_no_model(), media_type="text/event-stream")

    prompt = _build_prompt(move, meta)
    return StreamingResponse(
        _stream_ollama(chosen_model, prompt),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health():
    # Don't expose filesystem paths in a public-facing endpoint
    return {"status": "ok"}


# ═══════════════════════════════════════════════════════════════
# Game Recorder — click-to-move PGN builder
# ═══════════════════════════════════════════════════════════════

def _recorder_svg(board: chess.Board,
                  selected: Optional[chess.Square] = None,
                  last_move: Optional[chess.Move] = None) -> str:
    """Render board SVG with selected-square gold and legal-dest green dots."""
    fill = {}
    legal_dests: list[chess.Square] = []

    if selected is not None:
        fill[selected] = "#c9a84c66"          # gold — selected piece
        legal_dests = [
            m.to_square for m in board.legal_moves
            if m.from_square == selected
        ]
        for sq in legal_dests:
            fill[sq] = "#e0555555" if board.piece_at(sq) else "#4caf8233"

    return chess.svg.board(
        board,
        lastmove=last_move,
        fill=fill,
        squares=chess.SquareSet(legal_dests) if legal_dests else None,
        size=360,
        colors={
            "square light":          "#f0d9b5",
            "square dark":           "#b58863",
            "square light lastmove": "#cdd26a",
            "square dark lastmove":  "#aaa23a",
        },
    )


def _build_pgn(moves_uci: list[str], white: str, black: str) -> str:
    """Reconstruct a PGN string from a list of UCI move strings."""
    game  = chess.pgn.Game()
    game.headers["White"]  = white or "White"
    game.headers["Black"]  = black or "Black"
    game.headers["Result"] = "*"
    board = game.board()
    node  = game
    for uci in moves_uci:
        move = chess.Move.from_uci(uci)
        node = node.add_variation(move)
        board.push(move)
    # Set result if game is over
    if board.is_checkmate():
        result = "0-1" if board.turn == chess.WHITE else "1-0"
        game.headers["Result"] = result
    elif board.is_stalemate() or board.is_insufficient_material():
        game.headers["Result"] = "1/2-1/2"
    out = io.StringIO()
    print(game, file=out)
    return out.getvalue()


@app.get("/record")
async def serve_recorder():
    return FileResponse(str(Path(__file__).parent / "static" / "recorder.html"))


@app.post("/game/board-svg")
async def board_svg_endpoint(
    fen:       str = Form(...),
    last_move: str = Form(""),
    flipped:   str = Form("0"),
):
    """Render a board SVG for a given FEN, with optional last-move highlight."""
    try:
        board = chess.Board(fen)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid FEN.")
    last = None
    if last_move and len(last_move) >= 4:
        try:
            last = chess.Move.from_uci(last_move[:5])
        except ValueError:
            pass
    is_flipped = flipped == "1"
    svg = chess.svg.board(
        board,
        lastmove=last,
        flipped=is_flipped,
        size=360,
        colors={
            "square light":          "#f0d9b5",
            "square dark":           "#b58863",
            "square light lastmove": "#cdd26a",
            "square dark lastmove":  "#aaa23a",
        },
    )
    return {"svg": svg}


@app.post("/game/legal-moves")
async def legal_moves_endpoint(
    fen:    str = Form(...),
    square: str = Form(...),
):
    """
    Given a FEN and a square name (e.g. 'e2'), return legal destination
    squares and an updated SVG with the selection highlighted.
    """
    try:
        board = chess.Board(fen)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid FEN.")

    try:
        sq = chess.parse_square(square)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid square: {square!r}")

    # Only allow selecting pieces that belong to the side to move
    piece = board.piece_at(sq)
    if piece is None or piece.color != board.turn:
        return {"legal_dests": [], "svg": _recorder_svg(board)}

    legal_dests = [
        chess.square_name(m.to_square)
        for m in board.legal_moves
        if m.from_square == sq
    ]

    # Needs promotion if any legal move from this square is a pawn promo
    needs_promo = any(
        m.promotion is not None
        for m in board.legal_moves
        if m.from_square == sq
    )

    return {
        "legal_dests":  legal_dests,
        "needs_promo":  needs_promo,
        "svg":          _recorder_svg(board, selected=sq),
    }


@app.post("/game/move")
async def make_move_endpoint(
    fen:       str = Form(...),
    from_sq:   str = Form(...),
    to_sq:     str = Form(...),
    promotion: str = Form("q"),   # q r b n
    moves_uci: str = Form(""),    # space-separated history for PGN rebuild
    white:     str = Form("White"),
    black:     str = Form("Black"),
):
    """
    Validate and apply a move. Returns new FEN, SAN, updated SVG, and
    current PGN string.
    """
    try:
        board = chess.Board(fen)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid FEN.")

    try:
        fr = chess.parse_square(from_sq)
        to = chess.parse_square(to_sq)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid square name.")

    promo_piece = {"q": chess.QUEEN, "r": chess.ROOK,
                   "b": chess.BISHOP, "n": chess.KNIGHT}.get(promotion, chess.QUEEN)

    # Build the move — include promotion piece if this is a pawn reaching rank 8
    move = chess.Move(fr, to)
    promo_move = chess.Move(fr, to, promotion=promo_piece)
    if promo_move in board.legal_moves:
        move = promo_move
    elif move not in board.legal_moves:
        return {"legal": False, "message": "Illegal move."}

    san = board.san(move)
    board.push(move)

    # Rebuild full move history for PGN
    history = [m for m in moves_uci.split() if m] + [move.uci()]
    pgn = _build_pgn(history, white[:40], black[:40])

    # Game-over detection
    game_over = False
    game_over_msg = ""
    if board.is_checkmate():
        game_over = True
        game_over_msg = "Checkmate — " + ("Black wins." if board.turn == chess.WHITE else "White wins.")
    elif board.is_stalemate():
        game_over = True
        game_over_msg = "Stalemate — draw."
    elif board.is_insufficient_material():
        game_over = True
        game_over_msg = "Insufficient material — draw."
    elif board.is_seventyfive_moves():
        game_over = True
        game_over_msg = "75-move rule — draw."

    return {
        "legal":      True,
        "san":        san,
        "uci":        move.uci(),
        "new_fen":    board.fen(),
        "svg":        _recorder_svg(board, last_move=move),
        "pgn":        pgn,
        "in_check":   board.is_check(),
        "game_over":  game_over,
        "game_over_msg": game_over_msg,
        "moves_uci":  " ".join(history),
    }


# ── Static files ──────────────────────────────────────────────────────────────
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
