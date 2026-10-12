"""Registry writes from parallel workers must retain every update."""

import json
from concurrent.futures import ThreadPoolExecutor

from core.resource.registry.json_store import update_json_list


def test_parallel_json_registry_updates_are_not_lost(tmp_path):
    registry = tmp_path / "registry.json"

    def append(number):
        update_json_list(registry, lambda items: items.append({"id": number}))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append, range(40)))

    rows = json.loads(registry.read_text(encoding="utf-8"))
    assert {item["id"] for item in rows} == set(range(40))
