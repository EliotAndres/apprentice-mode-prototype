"""Chart for results/scores.json -> results/chart.png (and .svg).

One row per tier (easy: sequential tasks; hard: interleaved multitasking, same
tasks), one panel per headline metric with shared scales down each column.
Within a panel, input condition on x and one bar per Claude model. The no-LLM
heuristic (trace input only) is a dashed reference line.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

MODEL_COLORS = [("haiku", "#2a78d6"), ("sonnet", "#eb6834"), ("opus", "#1baf7a")]  # categorical slots 1-3
CONDITIONS = ["trace", "screenshots", "both"]
PANELS = [("r_at_10", "Recall@10 (95% CI)"), ("grounding_iou", "Evidence IoU"), ("p_at_5", "Precision@5"),
          ("fp_negative", "False positives on negatives (lower is better)")]
TIERS = [("easy", "Easy: one task at a time"), ("hard", "Hard: interleaved tasks")]
INK_COLOR, MUTED_COLOR, GRID_COLOR = "#0b0b0b", "#52514e", "#e7e6e2"
BAR_WIDTH = 0.26


def chart(scores: dict, output_path: Path) -> None:
    table = scores["table"]
    tiers_present = [(tier, tier_title) for tier, tier_title in TIERS
                     if any(table_row.get("tier", "easy") == tier for table_row in table)]
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 10, "axes.edgecolor": GRID_COLOR,
                         "axes.labelcolor": MUTED_COLOR, "xtick.color": MUTED_COLOR, "ytick.color": MUTED_COLOR})
    figure, axes_grid = plt.subplots(len(tiers_present), len(PANELS), figsize=(17, 4.1 * len(tiers_present)),
                                     squeeze=False)
    for (tier, tier_title), row_axes in zip(tiers_present, axes_grid):

        def table_value(model: str, condition: str, field: str, tier: str = tier):
            return next((table_row.get(field) for table_row in table if table_row.get("tier", "easy") == tier
                         and table_row["model"] == model and table_row["condition"] == condition), None)

        for column_index, (axis, (metric, panel_title)) in enumerate(zip(row_axes, PANELS)):
            y_limit = 0.4 if metric == "fp_negative" else 1
            for model_index, (model, color) in enumerate(MODEL_COLORS):
                bar_positions, bar_heights, error_bars = [], [], [[], []]
                for condition_index, condition in enumerate(CONDITIONS):
                    value = table_value(model, condition, metric)
                    if value is None:
                        continue
                    bar_positions.append(condition_index + (model_index - 1) * BAR_WIDTH)
                    bar_heights.append(value)
                    interval_low, interval_high = table_value(model, condition, f"{metric}_ci") or (value, value)
                    error_bars[0].append(value - interval_low)
                    error_bars[1].append(interval_high - value)
                axis.bar(bar_positions, bar_heights, BAR_WIDTH - 0.03, color=color, label=model, zorder=3)
                if metric == "r_at_10":
                    axis.errorbar(bar_positions, bar_heights, yerr=error_bars, fmt="none", ecolor=INK_COLOR,
                                  elinewidth=1, capsize=2.5, zorder=4)
            heuristic_value = table_value("heuristic", "trace", metric)
            if heuristic_value is not None:
                axis.axhline(heuristic_value, color=MUTED_COLOR, ls="--", lw=1.5, zorder=4,
                             label="heuristic (no LLM, trace)")
            axis.set_title(panel_title, loc="left", color=INK_COLOR, fontsize=10.5, fontweight="bold")
            if column_index == 0:
                axis.set_ylabel(tier_title, color=INK_COLOR, fontsize=11, fontweight="bold", labelpad=10)
            axis.set_xticks(range(len(CONDITIONS)), CONDITIONS)
            axis.set_ylim(0, y_limit)
            axis.grid(axis="y", color=GRID_COLOR, zorder=0)
            axis.tick_params(length=0)
            for spine_name in ("top", "right", "left"):
                axis.spines[spine_name].set_visible(False)
    legend_handles, legend_labels = axes_grid[0][0].get_legend_handles_labels()
    figure.legend(legend_handles, legend_labels, frameon=False, loc="upper right", ncols=4, fontsize=9.5,
                  bbox_to_anchor=(0.995, 0.995))
    figure.suptitle("Workflow discovery on chained Mind2Web travel sessions (8 per tier, same 29 workflows)",
                    x=0.01, ha="left", color=INK_COLOR, fontsize=12)
    figure.tight_layout(rect=(0, 0, 1, 0.97))
    figure.savefig(output_path.with_suffix(".png"), dpi=160)
    figure.savefig(output_path.with_suffix(".svg"))


if __name__ == "__main__":
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--scores", type=Path, default=Path("data/results/scores.json"))
    argument_parser.add_argument("--out", type=Path, default=Path("data/results/chart.png"))
    arguments = argument_parser.parse_args()
    chart(json.loads(arguments.scores.read_text()), arguments.out)
    print(f"wrote {arguments.out}")
