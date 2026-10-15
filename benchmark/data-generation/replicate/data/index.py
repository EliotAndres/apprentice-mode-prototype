"""Build a task-level index of Multimodal-Mind2Web from parquet metadata only.

Reads just the small columns (ids, task text, action reprs) over HTTP range
requests, so the ~8 GB of screenshots and HTML are never downloaded here.
Output: data/index/tasks.jsonl, one line per task (annotation_id), with the
shard/row-group/row of every action so fetch.py can pull pixels later.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow.parquet as parquet
from huggingface_hub import HfFileSystem

REPO = "datasets/osunlp/Multimodal-Mind2Web"
METADATA_COLUMNS = ["action_uid", "operation", "website", "domain", "subdomain", "annotation_id",
                    "confirmed_task", "action_reprs", "target_action_index", "target_action_reprs"]


def list_shards(filesystem: HfFileSystem) -> list[str]:
    return sorted(shard_path for shard_path in filesystem.ls(f"{REPO}/data", detail=False)
                  if shard_path.endswith(".parquet"))


def read_shard(shard_path: str) -> list[dict]:
    filesystem = HfFileSystem()
    with filesystem.open(shard_path, block_size=1 << 20) as shard_file:
        parquet_file = parquet.ParquetFile(shard_file)
        action_rows, rows_before_group = [], 0
        for row_group_index in range(parquet_file.num_row_groups):
            group_rows = parquet_file.read_row_group(row_group_index, columns=METADATA_COLUMNS).to_pylist()
            for row_in_group, action_row in enumerate(group_rows):
                action_row.update(shard=shard_path.rsplit("/", 1)[1], row_group=row_group_index,
                                  row_in_group=row_in_group, row=rows_before_group + row_in_group)
                action_rows.append(action_row)
            rows_before_group += len(group_rows)
    return action_rows


def build(output_path: Path, worker_count: int = 8) -> None:
    filesystem = HfFileSystem()
    shard_paths = list_shards(filesystem)
    tasks_by_id: dict[str, dict] = {}
    with ThreadPoolExecutor(worker_count) as executor:
        for shard_path, action_rows in zip(shard_paths, executor.map(read_shard, shard_paths)):
            print(f"{shard_path.rsplit('/', 1)[1]}: {len(action_rows)} actions", flush=True)
            for action_row in action_rows:
                task = tasks_by_id.setdefault(action_row["annotation_id"], {
                    "annotation_id": action_row["annotation_id"],
                    "split": action_row["shard"].split("-")[0],
                    "website": action_row["website"], "domain": action_row["domain"],
                    "subdomain": action_row["subdomain"], "task": action_row["confirmed_task"],
                    "action_reprs": action_row["action_reprs"], "actions": [],
                })
                task["actions"].append({
                    "index": int(action_row["target_action_index"]), "action_uid": action_row["action_uid"],
                    "repr": action_row["target_action_reprs"], "operation": json.loads(action_row["operation"]),
                    "shard": action_row["shard"], "row_group": action_row["row_group"],
                    "row_in_group": action_row["row_in_group"],
                })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as output_file:
        for task in tasks_by_id.values():
            task["actions"].sort(key=lambda action: action["index"])
            output_file.write(json.dumps(task) + "\n")
    print(f"wrote {len(tasks_by_id)} tasks to {output_path}")


if __name__ == "__main__":
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--out", type=Path, default=Path("data/index/tasks.jsonl"))
    build(argument_parser.parse_args().out)
