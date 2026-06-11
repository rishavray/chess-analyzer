#!/usr/bin/env python3
"""
Chess Analyzer — Iteration 1
Analyzes a PGN file using Stockfish and prints annotated move-by-move feedback.

Usage:
    python3 analyze.py game.pgn
    python3 analyze.py game.pgn --depth 18
    python3 analyze.py game.pgn --depth 18 --top-moves 3
"""

import sys
import argparse
import io
from pathlib import Path

try:
    import chess
    import chess.engine
    import chess.pgn
except ImportError:
    print("ERROR: python-chess not installed. Run: pip install chess")
    sys.exit(1)


# ─────────────────────────────────────────────
# Move classification thresholds (centipawns)
# Based on Lichess/chess.com classification logic
# ─────────────────────────────────────────────
THRESHOLDS = {
    "brilliant":   -999,   # engine's top move AND a sacrifice (we'll keep simple for now)
    "best":           0,   # exactly matches engine top move
    "excellent":     10,
    "good":          25,
    "inaccuracy":    50,
    "mistake":      100,
    "blunder":      300,   # anything above this
}

SYMBOLS = {
    "brilliant":  "!! 🌟",
    "best":       "  ✓",
    "excellent":  "!  ",
    "good":       "   ",
    "inaccuracy": "?! ⚠️ ",
    "mistake":    "?  ❌",
    "blunder":    "?? 💀",
}

COLORS = {
    "brilliant":  "\033[96m",   # cyan
    "best":       "\033[92m",   # green
    "excellent":  "\033[92m",   # green
    "good":       "\033[0m",    # normal
    "inaccuracy": "\033[93m",   # yellow
    "mistake":    "\033[91m",   # red
    "blunder":    "\033[91m",   # red (bold)
}
RESET = "\033[0m"
BOLD  = "\033[1m"


def classify_move(cp_loss: int) -> str:
    """Classify a move based on centipawn loss vs engine best."""
    if cp_loss <= 0:
        return "best"
    elif cp_loss <= 10:
        return "excellent"
    elif cp_loss <= 25:
        return "good"
    elif cp_loss <= 50:
        return "inaccuracy"
    elif cp_loss <= 100:
        return "mistake"
    else:
        return "blunder"


def score_to_cp(score: chess.engine.Score, board: chess.Board) -> float | None:
    """
    Convert a Score to centipawns from White's perspective.
    Returns None if it's a forced mate line.
    """
    if score.is_mate():
        mate_in = score.mate()
        # Assign large value preserving sign
        return 10000 if mate_in > 0 else -10000
    cp = score.score()
    # score() returns from the side-to-move perspective; we want White's POV
    if board.turn == chess.BLACK:
        cp = -cp
    return cp


def analyze_game(pgn_path: str, stockfish_path: str = "stockfish",
                 depth: int = 20, top_moves: int = 2) -> None:
    """Main analysis function."""

    # ── Load PGN ──────────────────────────────────────────────────────────────
    pgn_file = Path(pgn_path)
    if not pgn_file.exists():
        print(f"ERROR: File not found: {pgn_path}")
        sys.exit(1)

    with open(pgn_file) as f:
        game = chess.pgn.read_game(f)

    if game is None:
        print("ERROR: Could not parse PGN file.")
        sys.exit(1)

    headers = game.headers
    white   = headers.get("White", "White")
    black   = headers.get("Black", "Black")
    event   = headers.get("Event", "?")
    date    = headers.get("Date", "?")
    result  = headers.get("Result", "*")

    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  ♟  Chess Game Analysis{RESET}")
    print(f"{'═' * 60}")
    print(f"  Event : {event}  ({date})")
    print(f"  White : {white}")
    print(f"  Black : {black}")
    print(f"  Result: {result}")
    print(f"  Depth : {depth}  |  Engine: Stockfish")
    print(f"{'═' * 60}\n")

    # ── Start Stockfish ────────────────────────────────────────────────────────
    try:
        engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
    except FileNotFoundError:
        print(f"ERROR: Stockfish not found at '{stockfish_path}'.")
        print("       Install it or pass --stockfish /path/to/stockfish")
        sys.exit(1)

    # ── Walk through moves ─────────────────────────────────────────────────────
    board = game.board()
    move_number = 0

    # Stats tracking
    counts = {"brilliant": 0, "best": 0, "excellent": 0,
              "good": 0, "inaccuracy": 0, "mistake": 0, "blunder": 0}
    white_cp_losses = []
    black_cp_losses = []
    critical_moments = []  # (move_num, player, played_san, best_san, cp_loss)

    prev_cp = None  # White POV eval before the move

    all_moves = list(game.mainline_moves())
    total_moves = len(all_moves)

    print(f"  Analyzing {total_moves} moves...\n")

    for i, move in enumerate(all_moves):
        move_number = (i // 2) + 1
        is_white    = (i % 2 == 0)
        player      = white if is_white else black
        side_label  = "W" if is_white else "B"

        # Eval BEFORE the move (from White's POV)
        info_before = engine.analyse(board, chess.engine.Limit(depth=depth),
                                     multipv=top_moves)

        # multipv returns a list; take the best line
        if isinstance(info_before, list):
            best_info = info_before[0]
            alt_moves = [str(inf["pv"][0]) if inf.get("pv") else "?" 
                         for inf in info_before[1:]]
        else:
            best_info = info_before
            alt_moves = []

        score_before = best_info["score"].white()  # always White POV
        cp_before    = score_to_cp(score_before, board)

        best_move_uci = str(best_info["pv"][0]) if best_info.get("pv") else str(move)
        best_move_san = board.san(chess.Move.from_uci(best_move_uci))
        played_san    = board.san(move)

        # Make the move
        board.push(move)

        # Eval AFTER the move (from White's POV)
        info_after  = engine.analyse(board, chess.engine.Limit(depth=depth))
        score_after = (info_after[0] if isinstance(info_after, list) 
                       else info_after)["score"].white()
        cp_after    = score_to_cp(score_after, board)

        # Centipawn loss for the player who just moved
        if cp_before is not None and cp_after is not None:
            if is_white:
                cp_loss = cp_before - cp_after   # white wants cp to go up
            else:
                cp_loss = cp_after - cp_before   # black wants cp to go down (more negative)
            cp_loss = max(0, cp_loss)            # loss can't be negative
        else:
            cp_loss = 0  # mate line — skip detailed loss calc

        classification = classify_move(cp_loss)
        counts[classification] += 1

        if is_white:
            white_cp_losses.append(cp_loss)
        else:
            black_cp_losses.append(cp_loss)

        # Flag critical moments (mistakes + blunders)
        if classification in ("mistake", "blunder"):
            critical_moments.append((move_number, side_label, played_san,
                                     best_move_san, cp_loss, classification))

        # ── Format eval bar ──────────────────────────────────────────────────
        def fmt_eval(cp):
            if cp is None:
                return "   ?"
            if abs(cp) >= 9000:
                return "  #M" if cp > 0 else " -#M"
            return f"{cp/100:+.2f}"

        eval_str   = fmt_eval(cp_after)
        color      = COLORS.get(classification, "")
        symbol     = SYMBOLS.get(classification, "   ")
        move_label = f"{move_number}." if is_white else f"{move_number}..."

        # Build the line
        best_note = f"  (best: {best_move_san})" if played_san != best_move_san else ""
        loss_note = f"  [-{cp_loss/100:.2f}]" if cp_loss > 0 else ""

        print(f"  {color}{move_label:<5} {side_label}  "
              f"{played_san:<10} {symbol}   "
              f"eval {eval_str}{loss_note}{best_note}{RESET}")

    engine.quit()

    # ── Summary ────────────────────────────────────────────────────────────────
    def accuracy(cp_losses):
        """Simple accuracy estimate: 100 - avg_loss_per_move (capped)."""
        if not cp_losses:
            return 100.0
        avg = sum(cp_losses) / len(cp_losses)
        return max(0, min(100, 100 - (avg / 10)))

    w_acc = accuracy(white_cp_losses)
    b_acc = accuracy(black_cp_losses)

    print(f"\n{'═' * 60}")
    print(f"{BOLD}  Summary{RESET}")
    print(f"{'═' * 60}")
    print(f"  {'Category':<14} {'White':>6}  {'Black':>6}")
    print(f"  {'─' * 30}")
    for cat in ["brilliant", "best", "excellent", "good", "inaccuracy", "mistake", "blunder"]:
        # Re-count per player
        pass

    # Per-player breakdown
    board2 = game.board()
    w_counts = {k: 0 for k in counts}
    b_counts = {k: 0 for k in counts}

    board2 = game.board()
    prev_cp2 = None

    # Quick re-run for per-player stats using stored cp_losses
    # (we already have white_cp_losses / black_cp_losses lists)
    w_blunders    = sum(1 for x in white_cp_losses if x > 300)
    w_mistakes    = sum(1 for x in white_cp_losses if 100 < x <= 300)
    w_inaccuracies= sum(1 for x in white_cp_losses if 50 < x <= 100)

    b_blunders    = sum(1 for x in black_cp_losses if x > 300)
    b_mistakes    = sum(1 for x in black_cp_losses if 100 < x <= 300)
    b_inaccuracies= sum(1 for x in black_cp_losses if 50 < x <= 100)

    print(f"  {'Blunders':<14} {w_blunders:>6}  {b_blunders:>6}   💀")
    print(f"  {'Mistakes':<14} {w_mistakes:>6}  {b_mistakes:>6}   ❌")
    print(f"  {'Inaccuracies':<14} {w_inaccuracies:>6}  {b_inaccuracies:>6}   ⚠️")
    print(f"  {'─' * 30}")
    print(f"  {'Accuracy':<14} {w_acc:>5.1f}%  {b_acc:>5.1f}%")
    print(f"  {'─' * 30}")
    print(f"  {white:<14} vs  {black}")

    if critical_moments:
        print(f"\n{'═' * 60}")
        print(f"{BOLD}  Critical Moments{RESET}")
        print(f"{'═' * 60}")
        for (mn, side, played, best, loss, cls) in critical_moments:
            label = "White" if side == "W" else "Black"
            tag   = "BLUNDER" if cls == "blunder" else "MISTAKE"
            print(f"  Move {mn} ({label}): {played} [{tag}]")
            print(f"    → Engine preferred {best}  (cost: {loss/100:.2f} pawns)")

    print(f"\n{'═' * 60}\n")


# ─────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Analyze a chess game PGN with Stockfish."
    )
    parser.add_argument("pgn", help="Path to the .pgn file")
    parser.add_argument("--depth",      type=int, default=20,
                        help="Stockfish analysis depth (default: 20)")
    parser.add_argument("--top-moves",  type=int, default=2,
                        help="Number of top moves to consider (default: 2)")
    parser.add_argument("--stockfish",  type=str, default="stockfish",
                        help="Path to Stockfish binary (default: stockfish)")
    args = parser.parse_args()

    analyze_game(
        pgn_path       = args.pgn,
        stockfish_path = args.stockfish,
        depth          = args.depth,
        top_moves      = args.top_moves,
    )


if __name__ == "__main__":
    main()
