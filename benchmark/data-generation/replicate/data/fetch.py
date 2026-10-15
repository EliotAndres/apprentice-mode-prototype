"""Fetch screenshots, cleaned DOM and target elements for the actions a plan uses.

Only the parquet row groups that contain a needed action are read, and only
the columns we keep (HTTP range reads through HfFileSystem). A row group holds
~100 full-page screenshots (~90 MB), so 8 sessions cost ~6.5 GB of transfer
and nothing is kept but the crops. (The datasets-server /rows API would be
lighter per row but rate-limits with HTTP 429 after ~70 requests.) Per action:
  data/raw/frames/<action_uid>.jpg   1280x800 viewport crop around the target
  data/raw/dom/<action_uid>.html.gz  cleaned HTML snapshot
  data/raw/meta/<action_uid>.json    target tag/attributes, bbox in the crop
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")  # must precede the huggingface_hub import

import pyarrow.parquet as parquet  # noqa: E402
from huggingface_hub import HfFileSystem  # noqa: E402
from PIL import Image  # noqa: E402

from replicate.data.index import REPO  # noqa: E402

COLUMNS = ["action_uid", "cleaned_html", "pos_candidates", "screenshot"]
VIEWPORT_WIDTH, VIEWPORT_HEIGHT = 1280, 800
RAW_DIRECTORY = Path("data/raw")


def crop_viewport(page_image: Image.Image, target_bbox: list[float] | None) -> tuple[Image.Image, list[float] | None]:
    """Cut the 1280x800 window a user would have been looking at when acting on target_bbox."""
    page_image = page_image.convert("RGB")
    page_width, page_height = page_image.size
    viewport_top = 0
    if target_bbox:
        target_center_y = target_bbox[1] + target_bbox[3] / 2
        viewport_top = int(min(max(target_center_y - VIEWPORT_HEIGHT / 2, 0), max(page_height - VIEWPORT_HEIGHT, 0)))
    viewport = page_image.crop((0, viewport_top, min(page_width, VIEWPORT_WIDTH),
                                min(page_height, viewport_top + VIEWPORT_HEIGHT)))
    if viewport.size != (VIEWPORT_WIDTH, VIEWPORT_HEIGHT):
        canvas = Image.new("RGB", (VIEWPORT_WIDTH, VIEWPORT_HEIGHT), "white")
        canvas.paste(viewport, (0, 0))
        viewport = canvas
    viewport_bbox = ([target_bbox[0], target_bbox[1] - viewport_top, target_bbox[2], target_bbox[3]]
                     if target_bbox else None)
    return viewport, viewport_bbox


def save_action(action_row: dict) -> None:
    action_uid = action_row["action_uid"]
    target_element = json.loads(action_row["pos_candidates"][0]) if action_row["pos_candidates"] else None
    attributes = json.loads(target_element["attributes"]) if target_element else {}
    target_bbox = ([float(coordinate) for coordinate in attributes["bounding_box_rect"].split(",")]
                   if attributes.get("bounding_box_rect") else None)
    if target_bbox and target_bbox[2] <= 0:
        target_bbox = None
    page_image = Image.open(io.BytesIO(action_row["screenshot"]["bytes"]))
    page_size = list(page_image.size)
    viewport, viewport_bbox = crop_viewport(page_image, target_bbox)
    for subdirectory in ("frames", "dom", "meta"):
        (RAW_DIRECTORY / subdirectory).mkdir(parents=True, exist_ok=True)
    viewport.save(RAW_DIRECTORY / "frames" / f"{action_uid}.jpg", quality=72, optimize=True)
    (RAW_DIRECTORY / "dom" / f"{action_uid}.html.gz").write_bytes(gzip.compress(action_row["cleaned_html"].encode(), 6))
    kept_attributes = {name: value for name, value in attributes.items()
                       if name in ("id", "role", "aria_label", "name", "type", "placeholder", "class", "value", "title")}
    (RAW_DIRECTORY / "meta" / f"{action_uid}.json").write_text(json.dumps({
        "action_uid": action_uid, "tag": target_element["tag"] if target_element else None,
        "attributes": kept_attributes, "bbox": viewport_bbox, "page_size": page_size}))


def fetch_row_group(shard_name: str, row_group_index: int, action_uid_by_row: dict[int, str]) -> int:
    filesystem = HfFileSystem()
    with filesystem.open(f"{REPO}/data/{shard_name}", block_size=128 << 20) as shard_file:
        table = parquet.ParquetFile(shard_file).read_row_group(row_group_index, columns=COLUMNS)
    for row_in_group, expected_action_uid in action_uid_by_row.items():
        action_row = {column: table.column(column)[row_in_group].as_py() for column in COLUMNS}
        if action_row["action_uid"] != expected_action_uid:
            raise ValueError(f"{shard_name}/{row_group_index}/{row_in_group}: expected {expected_action_uid}, "
                             f"got {action_row['action_uid']}")
        save_action(action_row)
    return len(action_uid_by_row)


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--index", type=Path, default=Path("data/index/tasks.jsonl"))
    argument_parser.add_argument("--plans", type=Path, default=Path("data/plans"))
    argument_parser.add_argument("--workers", type=int, default=6)
    arguments = argument_parser.parse_args()
    task_by_id = {task["annotation_id"]: task for task in map(json.loads, arguments.index.open())}
    actions_by_row_group: dict[tuple[str, int], dict[int, str]] = defaultdict(dict)
    for plan_path in sorted(arguments.plans.glob("*.json")):
        for segment in json.loads(plan_path.read_text())["segments"]:
            for action_index in segment["actions"]:
                action = task_by_id[segment["task_id"]]["actions"][action_index]
                if not (RAW_DIRECTORY / "meta" / f"{action['action_uid']}.json").exists():
                    actions_by_row_group[action["shard"], action["row_group"]][action["row_in_group"]] = \
                        action["action_uid"]
    print(f"{sum(map(len, actions_by_row_group.values()))} actions to fetch from "
          f"{len(actions_by_row_group)} row groups", flush=True)
    finished_groups = 0
    with ThreadPoolExecutor(arguments.workers) as executor:
        fetch_jobs = executor.map(
            lambda group_item: fetch_row_group(group_item[0][0], group_item[0][1], group_item[1]),
            actions_by_row_group.items())
        for fetched_action_count in fetch_jobs:
            finished_groups += 1
            print(f"  row group {finished_groups}/{len(actions_by_row_group)} (+{fetched_action_count} actions)",
                  flush=True)
    print("done")


if __name__ == "__main__":
    main()
