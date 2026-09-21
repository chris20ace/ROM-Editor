# World canvas alignment investigation

The earlier Route 103 / Route 110 tear had two causes: an incompatible loop in the original map coordinates, and a canvas placement rule that exposed that loop on a walkable path. The terrain import has not lost two rows. The editor now arranges complete maps in separate sections on one canvas, retaining the original links between sections.

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

## The earlier placement mistake

The earlier `worldmap.py` placement rule scored candidates by `(number of conflicting joins, joins involving towns/cities, overlapping rectangle area, displacement)`. It temporarily omitted connection constraints while positioning maps, then reported the remaining disagreement.

That rule favored town boundaries and avoided rectangle overlaps without considering whether a connection was traversable. It selected Route 103 / Route 110 for the remaining break. This made a playable path look damaged. Earlier changes moved the visible discrepancy between locations; they did not resolve the original coordinate contradiction.

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

This investigation leaves map geometry, connections, terrain, sprites, and the ROM unchanged. The rejected display projection remains removed.

## What the source groups mean

`data/maps/map_groups.json` defines the map lookup groups used by `Overworld_GetMapHeaderByGroupAndId` (`src/overworld.c`, lines 579–581). For example, `gMapGroup_TownsAndRoutes` contains the surface towns and routes together with underwater maps, while `gMapGroup_Dungeons` contains unrelated caves. These groups identify maps in the ROM; they do not declare rectangular world regions that can be stitched together.

The `region_map_section` fields and `src/data/region_map/region_map_sections.json` describe names and locations on the PokéNav/Fly picture. Their small illustration-grid coordinates are separate from the playable maps' tile coordinates. They are useful labels, but do not supply missing global terrain positions.

`fieldmap.c` reads a map's dimensions and its local connection records to compose the current view. It does not define the editor's display sections.

## Current single-canvas arrangement

Every source map appears once on the same canvas, with its entire rectangular tile grid. The editor groups maps into sections whose internal walking connections can all use their source offsets without overlapping complete map rectangles. Section placement is an editor arrangement calculated from source dimensions and connections.

Original connections that run between sections remain explicit links between their source and destination maps. A gap between sections is display spacing; it does not disconnect the game or change its travel offsets. Route 103 retains all 80 × 22 cells, its south connection to Oldale at offset 0, and its east connection to Route 110 at offset −60, wherever the section boundary falls.

All sections, interiors and other detached areas stay on the same editable page. Selecting a map focuses its position instead of replacing the world with a neighborhood view. Terrain and event buffers and Undo history stay attached to the original map names and source coordinates.

This arrangement preserves the original terrain and local joins without claiming that all 518 maps form one globally seamless surface. It does not stretch, shear, crop, duplicate or conceal source terrain to close the inconsistent loop, and it does not modify source files merely to arrange the canvas.
