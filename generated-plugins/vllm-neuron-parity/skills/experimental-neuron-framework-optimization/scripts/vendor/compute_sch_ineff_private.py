# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License").
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#!/usr/bin/env python3
"""Compute scheduling inefficiency by comparing unseparated vs separated test runs.

Reads two QoR CSV files:
  1. Unseparated run: uses ActiveInferenceTime
  2. Separated run: uses max(SeparatedComputeTime, SeparatedMemoryTime)

Outputs a CSV with TestName, ActiveInferenceTime, SeparatedBound, BoundBy, SchedulingInefficiency (%).
"""

import csv
import glob
import sys
from pathlib import Path
from typing import TypedDict

# One output record; the last field name is not an identifier, hence the functional syntax.
_ResultRow = TypedDict(
    "_ResultRow",
    {
        "TestName": str,
        "ActiveInferenceTime": float,
        "SeparatedBound": float,
        "BoundBy": str,
        "SchedulingInefficiency (%)": float,
    },
)


def find_latest_qor(directory):
    files = sorted(glob.glob(str(Path(directory) / "qor_data_*.csv")))
    return files[-1] if files else None


def read_qor(path):
    records = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            records[row["TestName"]] = row
    return records


def main():
    unsep_dir = sys.argv[1]
    sep_dir = sys.argv[2]
    out_path = sys.argv[3]

    unsep_csv = find_latest_qor(unsep_dir)
    sep_csv = find_latest_qor(sep_dir)
    if not unsep_csv or not sep_csv:
        print(f"ERROR: QoR CSV not found (unsep={unsep_csv}, sep={sep_csv})", file=sys.stderr)
        sys.exit(1)

    unsep = read_qor(unsep_csv)
    sep = read_qor(sep_csv)

    rows: list[_ResultRow] = []
    for name in unsep:
        if name not in sep:
            continue
        active_time = float(unsep[name].get("ActiveInferenceTime", -1))
        compute = float(sep[name].get("SeparatedComputeTime", -1))
        memory = float(sep[name].get("SeparatedMemoryTime", -1))
        if active_time <= 0 or compute <= 0 or memory <= 0:
            continue
        bound = max(compute, memory)
        bound_by = "compute" if compute >= memory else "memory"
        inefficiency = ((active_time - bound) / bound) * 100
        rows.append(
            {
                "TestName": name,
                "ActiveInferenceTime": active_time,
                "SeparatedBound": bound,
                "BoundBy": bound_by,
                "SchedulingInefficiency (%)": round(inefficiency, 2),
            }
        )

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["TestName", "ActiveInferenceTime", "SeparatedBound", "BoundBy", "SchedulingInefficiency (%)"]
        )
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows to {out_path}")
    for r in rows:
        print(
            f"  {r['TestName'][:80]}  active={r['ActiveInferenceTime']:.9f}  bound={r['SeparatedBound']:.9f}  bound_by={r['BoundBy']}  ineff={r['SchedulingInefficiency (%)']:.2f}%"
        )


if __name__ == "__main__":
    main()
