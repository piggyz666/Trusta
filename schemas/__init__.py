"""Single local schema definition shared by Planner, TaskGraph and Validator."""
import json
from pathlib import Path
from llm.hashing import snapshot
from llm.providers.common import check_schema

_PLAN = snapshot(json.loads((Path(__file__).parent / 'task_plan.json').read_text(encoding='utf-8')))
check_schema(json.loads(_PLAN))


def task_plan_schema():
    return json.loads(_PLAN)


def task_node_schema():
    return task_plan_schema()['$defs']['TaskNode']
