"""Compose map saves into one validated, conflict-free source transaction.

The caller must hold the project lock while planning and committing. This module
never commits, writes files, or makes one map's proposal visible to the next map.
"""
from __future__ import annotations

import re

from workspace_edits import protected_species


MAX_BATCH_MAPS = 128
_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")


def plan_world_batch(world, bodies):
    """Return one plan for 1–128 distinct existing maps, or raise without writes.

Each body is the ordinary ``World.plan_save`` body plus a ``name``. All maps use
their own saved revisions. New cross-map warp references must already be valid
in the saved source; this planner does not stage one proposal for another.
Identical writes to shared files coalesce. Different proposed bytes for a shared
file are rejected, rather than choosing one map's version or merging silently.
"""
    if not isinstance(bodies, list) or not 1 <= len(bodies) <= MAX_BATCH_MAPS:
        raise ValueError(f"Save between 1 and {MAX_BATCH_MAPS} maps in one world batch")
    names = set()
    for body in bodies:
        if not isinstance(body, dict):
            raise ValueError("Each world map edit must be an object")
        name = body.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("Each world map edit needs a valid map name")
        if name in names:
            raise ValueError(f"Duplicate map in world batch: {name}")
        names.add(name)

    merged, owners = {}, {}
    for body in bodies:
        name = body["name"]
        try:
            plan = world.plan_save(name, body)
        except ValueError as error:
            raise ValueError(f"Cannot save {name}: {error}") from error
        if not isinstance(plan, dict):
            raise ValueError(f"Map {name} returned an invalid source plan")
        for path, content in plan.items():
            if (not isinstance(path, str) or "\\" in path or ":" in path
                    or any(part in {"", ".", ".."} or part.startswith(".") for part in path.split("/"))):
                raise ValueError("World save plan contains an invalid source path")
            if protected_species(path):
                raise ValueError("Pokémon definitions are protected in a world save")
            if not isinstance(content, bytes) or len(content) > 8 * 1024 * 1024:
                raise ValueError(f"Map {name} returned invalid file contents for {path}")
            if path in merged and merged[path] != content:
                raise ValueError(f"Maps {owners[path]} and {name} propose different contents for shared file {path}. Reconcile those edits before saving.")
            merged[path] = content
            owners.setdefault(path, name)
    return merged
