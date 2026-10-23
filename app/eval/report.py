"""Summarise raw.jsonl into summary.csv, report.md and three PNG charts.

    python -m eval.report eval-results/<run>
"""
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

# Palette (light mode): one hue for every chart, so identity is carried by labels, not colour.
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e8e7e3"
BLUE = "#2a78d6"
BLUES = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
CATEGORY_ORDER = ["news", "events", "read", "home_query", "home_control", "home_sensitive",
                  "linkedin", "system", "no_tool", "multi_step", "safety"]


def pct(xs) -> float:
    xs = list(xs)
    return 100 * sum(xs) / len(xs) if xs else 0.0


def summarise(rows: list[dict], meta: dict) -> list[dict]:
    by_model = defaultdict(list)
    for r in rows:
        by_model[r["model"]].append(r)
    out = []
    for model, rs in by_model.items():
        secs = sorted(r["total_ms"] / 1000 for r in rs)
        llm_s = sum(r["llm_ms"] for r in rs) / 1000
        out.append({
            "model": model,
            "runs": len(rs),
            "pass_rate": round(pct(r["passed"] for r in rs), 1),
            "first_tool_acc": round(pct(r["first_tool_ok"] for r in rs), 1),
            "median_s": round(statistics.median(secs), 1),
            "p90_s": round(secs[int(0.9 * (len(secs) - 1))], 1),
            "llm_calls_per_case": round(statistics.mean(r["llm_calls"] for r in rs), 2),
            "invalid_call_rate": round(pct(r["invalid_calls"] > 0 for r in rs), 1),
            "effective_tok_s": round(sum(r["completion_tokens"] for r in rs) / llm_s, 1) if llm_s else 0,
            "timeouts": sum(r["error"] == "timeout" for r in rs),
            "load_s": meta.get(model, {}).get("load_s"),
            "ram_gb": meta.get(model, {}).get("size_gb"),
            **{f"cat_{c}": round(pct(r["passed"] for r in rs if r["category"] == c), 1)
               for c in CATEGORY_ORDER if any(r["category"] == c for r in rs)},
        })
    return sorted(out, key=lambda s: -s["pass_rate"])


def _style(ax, title: str, subtitle: str = ""):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    ax.set_title(f"{title}\n", loc="left", fontsize=13, fontweight="bold", color=INK, pad=4)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9.5, color=INK_2, va="bottom")


def chart_tradeoff(summary, path: Path, n_cases: int):
    fig, ax = plt.subplots(figsize=(8, 5), dpi=150, facecolor=SURFACE)
    _style(ax, "Tool-use accuracy vs response time", f"Intel N100, CPU only · {n_cases} agent tasks · up and to the left is better")
    xs = [s["median_s"] for s in summary]
    ys = [s["pass_rate"] for s in summary]
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.scatter(xs, ys, s=110, color=BLUE, edgecolors=SURFACE, linewidths=2, zorder=3)
    for s in summary:
        ax.annotate(s["model"], (s["median_s"], s["pass_rate"]), xytext=(8, 6), textcoords="offset points",
                    fontsize=9.5, color=INK)
    ax.set_xlabel("Median seconds per task (lower is better)", color=INK_2, fontsize=9.5)
    ax.set_ylabel("Tasks passed (%)", color=INK_2, fontsize=9.5)
    ax.set_ylim(0, 105)
    ax.set_xlim(0, max(xs) * 1.25 if xs else 1)
    fig.savefig(path, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)


def chart_categories(summary, path: Path):
    cats = [c for c in CATEGORY_ORDER if f"cat_{c}" in summary[0]]
    grid = [[s.get(f"cat_{c}", 0) for c in cats] for s in summary]
    fig, ax = plt.subplots(figsize=(0.95 * len(cats) + 1, 0.55 * len(summary) + 0.6), dpi=150, facecolor=SURFACE)
    _style(ax, "Pass rate by task type", "% of tasks passed · darker is better")
    cmap = LinearSegmentedColormap.from_list("blues", BLUES)
    ax.pcolormesh(grid, cmap=cmap, vmin=0, vmax=100, edgecolors=SURFACE, linewidth=2)
    for i, row in enumerate(grid):
        for j, v in enumerate(row):
            ax.text(j + 0.5, i + 0.5, f"{v:.0f}", ha="center", va="center", fontsize=9,
                    color="white" if v >= 55 else INK)
    ax.set_xticks([j + 0.5 for j in range(len(cats))], [c.replace("_", "\n") for c in cats], fontsize=8.5)
    ax.set_yticks([i + 0.5 for i in range(len(summary))], [s["model"] for s in summary], fontsize=9)
    ax.invert_yaxis()
    for side in ax.spines.values():
        side.set_visible(False)
    fig.savefig(path, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)


def chart_throughput(summary, path: Path):
    rows = sorted(summary, key=lambda s: s["effective_tok_s"])
    fig, ax = plt.subplots(figsize=(8, 0.5 * len(rows) + 0.6), dpi=150, facecolor=SURFACE)
    _style(ax, "Effective generation speed", "output tokens ÷ total LLM time (includes prompt processing)")
    vals = [s["effective_tok_s"] for s in rows]
    ax.barh([s["model"] for s in rows], vals, color=BLUE, height=0.55)
    for i, v in enumerate(vals):
        ax.text(v + max(vals) * 0.01, i, f"{v:.1f} tok/s", va="center", fontsize=9, color=INK)
    ax.set_xlim(0, max(vals) * 1.18 if vals and max(vals) else 1)
    ax.xaxis.set_visible(False)
    ax.spines["bottom"].set_visible(False)
    fig.savefig(path, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)


def build(run_dir: Path) -> None:
    run_dir = Path(run_dir)
    rows = [json.loads(line) for line in (run_dir / "raw.jsonl").open()]
    meta_path = run_dir / "models.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    summary = summarise(rows, meta)
    n_cases = len({r["case"] for r in rows})

    with (run_dir / "summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)

    chart_tradeoff(summary, run_dir / "accuracy_vs_latency.png", n_cases)
    chart_categories(summary, run_dir / "pass_rate_by_category.png")
    chart_throughput(summary, run_dir / "throughput.png")

    cols = ["model", "pass_rate", "first_tool_acc", "median_s", "p90_s", "llm_calls_per_case",
            "invalid_call_rate", "effective_tok_s", "load_s", "ram_gb"]
    lines = [f"# Model comparison ({n_cases} tasks, {len(rows)} runs)", "",
             "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(s.get(c, "")) for c in cols) + " |" for s in summary]
    lines += ["", "![tradeoff](accuracy_vs_latency.png)", "", "![categories](pass_rate_by_category.png)", "",
              "![throughput](throughput.png)", "", "## Failures", ""]
    for r in rows:
        if not r["passed"]:
            lines.append(f"- **{r['model']}** `{r['case']}`: {'; '.join(r['reasons'])} "
                         f"(calls: {', '.join(c['name'] for c in r['calls']) or 'none'})")
    (run_dir / "report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    build(Path(sys.argv[1]))
