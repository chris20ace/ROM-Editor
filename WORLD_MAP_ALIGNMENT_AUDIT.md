# World canvas alignment investigation

The displayed Route 103 / Route 110 tear has two distinct causes: an incompatible loop in the original map coordinates, and a canvas placement rule that chooses to expose that loop on a walkable path. The terrain import has not lost two rows. Warping the terrain was not a valid correction and has been reverted.

## Evidence from the original ROM

The supplied Emerald USA/Europe ROM has SHA-1 `f3ae088181bf583e55daf962a92bb46f4f1d07b7`.

- Route 103's layout record at ROM file offset `0x3ED234` says **80 columns, 22 rows**. Its terrain is 3,520 bytes: 80 × 22 × 2 bytes per cell.
- Route 103 connects south to Oldale with offset 0 and east to Route 110 with offset −60.
- Route 110 is **40 × 100**, with the reciprocal west connection to Route 103 at offset +60.
- These values agree with the local decompilation source used by the editor.

The direct binary audit covered **all 518 maps**: layout IDs, dimensions, full terrain-block bytes, border bytes, and all **148 connection records** match the source. The main connected world contains 49 maps and 112 directed cardinal connection records. There were zero mismatches in those checks. This is not a comparison of every script or tileset graphic against a newly compiled ROM.

The diagnostic locates the terrain block at `0x3EC474`, follows its layout and map-header references, and discovers the master map-group table at `0x486578`. It decodes the connections from the ROM and independently reproduces the `(0, +2)` closed-loop result below.

Reproduce with `python diagnostics/verify_rom_maps.py --rom "path/to/Pokemon - Emerald Version (USA, Europe).gba" --report .workbench/rom-map-verification.json`. The script reads the ROM and source; it only writes the optional diagnostic report outside those inputs. The existing local report contains the per-map binary offsets and comparison results.

These are map-local coordinates. They are not a stored, authoritative position of each map on one world-wide grid.

## An independent check of the loop

Take Route 103's top-left corner as `(0, 0)`, with Y increasing south. Summing only the source dimensions and connection offsets, without the editor's placement optimizer, gives:

| Connection | Change in X | Change in Y |
| --- | ---: | ---: |
| Route 103 → Oldale | 0 | +22 |
| Oldale → Route 102 | −50 | 0 |
| Route 102 → Petalburg | −30 | −10 |
| Petalburg → Route 104 | −40 | −50 |
| Route 104 → Rustboro | 0 | −60 |
| Rustboro → Route 116 | +40 | 0 |
| Route 116 → Verdanturf | +80 | +20 |
| Verdanturf → Route 117 | +20 | 0 |
| Route 117 → Mauville | +60 | 0 |
| Mauville → Route 110 | 0 | +20 |
| **Total through the other maps** | **+80** | **−58** |
| **Direct Route 103 → Route 110** | **+80** | **−60** |

The two paths require Route 110 to occupy positions two tiles apart. A single rigid placement for each complete map cannot satisfy both. The discrepancy is in the original local connection constraints, not a rounding error in the canvas.

## Border padding is not the missing explanation

`source/pokeemerald/src/fieldmap.c` copies all source rows into a temporary buffer at `(7, 7)` (lines 112–117). It puts the south neighbor after the full source height (189–210) and the east neighbor after the full source width (294–314). `include/fieldmap.h` defines the temporary padding as seven tiles north/south and seven/eight west/east.

`SetPositionFromConnection` and `CameraMove` (fieldmap.c 578–630) change map-local coordinates using the full dimensions and stored offsets. There is no two-row trim or overlap correction. The workbench's `_delta` formulas match this engine behavior, and `world.py` renders every source terrain row.

## The mistake in the canvas

`worldmap.py` scores candidate placements by `(number of conflicting joins, joins involving towns/cities, overlapping rectangle area, displacement)` (lines 91–111). It temporarily omits connection constraints while positioning maps, then reports the remaining disagreement.

That rule favors town boundaries and avoids rectangle overlaps without considering whether a connection is traversable. It selected Route 103 / Route 110 for the remaining break. This made a playable path look damaged. Earlier changes moved the visible discrepancy between locations; they did not resolve the original coordinate contradiction.

The Route 116 / Verdanturf boundary is blocked across its full 20-tile width. Route 103 / Route 110 has matching passable cells along the path. That distinction explains why the selected break is particularly unsuitable for an editor.

Allowing Route 116 and Verdanturf to overlap by two rows does not provide a lossless duplicate-border merge either: only **4 of the 40** overlapping terrain cells have matching metatile IDs. Covering one strip with the other would conceal distinct original terrain.

## Version comparison

The upstream Ruby layout lists Route 103 as 80 × 20; Emerald lists it as 80 × 22. Both use the same −60 east connection. That comparison is consistent with the two-row discrepancy, but it does not establish the original developers' intent. Replacing Emerald's height with Ruby's would alter real Emerald terrain and is not an import correction.

Primary references:

- [Emerald layouts at the project's pinned source revision](https://github.com/pret/pokeemerald/blob/5eff78649e7170a877b961ef0b3da13b81a16038/data/layouts/layouts.json#L184-L192)
- [Emerald Route 103 connections](https://github.com/pret/pokeemerald/blob/5eff78649e7170a877b961ef0b3da13b81a16038/data/maps/Route103/map.json)
- [Emerald field-map engine](https://github.com/pret/pokeemerald/blob/5eff78649e7170a877b961ef0b3da13b81a16038/src/fieldmap.c)
- [Ruby layouts](https://github.com/pret/pokeruby/blob/master/data/layouts/layouts.json)
- [Ruby Route 103 connections](https://github.com/pret/pokeruby/blob/master/data/maps/Route103/map.json)

This investigation leaves map geometry, connections, terrain, sprites, and the ROM unchanged. The rejected display projection remains removed. A future implementation must distinguish map-local connections from a world overview and must not silently stretch, trim, overlap, or relocate terrain to claim a seamless original world.

## Editor correction after the audit

The canvas now offers an explicit **Connected view** using a selected map and its immediate neighbors at the original connection offsets. Route 103 / Route 110 can be edited in that aligned view with full rectangular grids and no terrain transformation or source change. Buffers and Undo history are shared with **World overview**, which still contains every map. The local editing fix does not claim the original global loop is geometrically consistent.
