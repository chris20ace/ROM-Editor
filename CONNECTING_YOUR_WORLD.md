# Building towns, routes, and the places inside them

Start in the [World canvas](http://127.0.0.1:8765/worldmap). Every map is available there, organized into **Towns & cities**, **Routes**, **Caves & landmarks**, and **Other areas**. This includes homes, shops, gyms, cave floors, underwater maps, secret bases and special facilities. Connected landscapes keep their walking connections; separate rooms and floors sit in labeled area groups on the same canvas. Their display arrangement does not invent new travel links.

Zoom out to see everything, or select an area or map to focus it. Paint terrain and edit people directly without changing pages. Save world writes the edited maps together. Emerald's original route loop does not close on a single flat grid. The canvas minimizes display mismatches, preserves town boundaries, and marks the remaining shifted join in amber. Choose **shifted map join · inspect** to see its actual connection alignment or focus the location. This changes only the world-view arrangement, not source map dimensions or travel links.

Choose **Edit story, settings & more** to open the selected place's dialogue, map settings, doors and stairs, or buildings in a panel over the world. The same panel includes starters, gym leaders, player appearance, encounters, items, music references and source logic. **Back to world** keeps unfinished forms available and refreshes saved changes. Each editor has its own Save button.

Scroll to zoom, hold Space and drag to move around, or click a place in the sidebar to focus it. Choose a landscape tile to paint. **People & events** lets you place, move and remove people, objects and markers on the same world canvas. **New blank place** adds a blank town or route alongside the original world; connect it through **Connections** when you decide where it belongs.

[Towns & routes](http://127.0.0.1:8765/areas) is the organized area browser. Each area has its complete outdoor landscape and a grouped list of its interiors. Homes, shops, Pokémon Centers, gyms, story buildings, caves, and floors stay under the area they belong to. Search also finds a building inside an area.

- **Edit entire town / route** focuses its main map on the world canvas. Paint the land, buildings, roads, trees and water; place people and objects. Its interior cards open those rooms on the same canvas. Map resizing and advanced map settings are also available in World studio.
- **Add interior** creates a separate blank room using a house, shop, gym, Pokémon Center, laboratory or cave tileset. Keep the area's name prefix so the room stays grouped with its area.
- **New town / New route** creates a blank outdoor map and its own area workspace. These preserve the existing world. New maps initially borrow the template's music and region-map section; update those map settings when assigning the new place its own identity.
- Each interior has its own **Edit map**, **Doors & links**, and **Story** links.

## How the places connect

The walking world is a set of maps. Two different links join them:

```text
                       ROUTE 101
                           ⇅
                      town edge
                           ⇅
                     LITTLEROOT
                           ⇅
                     a house door
                           ⇅
                    HOUSE INTERIOR
                           ⇅
                         stairs
                           ⇅
                     UPSTAIRS ROOM
```

**Route and town edges:** walking off one map enters the neighboring map. In [Connect places](http://127.0.0.1:8765/connections), choose the maps, which side they join on, and their alignment. Save the connection to write both directions together. Moving a preview is a planning action until you save. An occupied overlapping edge must be disconnected before assigning it to another map. Paths at the joining edge still need matching walkable terrain and elevation.

**Doors, stairs and cave entrances:** an entrance marker points to another entrance marker on a separate map. Choose both maps, select an existing marker or a new tile on each preview, then save the pair. The editor handles the destination indices and return link. Rewiring a door keeps other markers and their numbering; an old counterpart can still lead into the original map until you change it too. Some existing game warps intentionally work in only one direction.

An entrance also needs the appropriate terrain tile. A marker on ordinary grass does not turn that grass into a working door. Paint a door, stair or cave warp tile in the terrain editor. Keep the arrival area accessible. Emerald's animated exterior doors are entered by walking upward into the door; the player walks down one tile when emerging, so leave space below the door. Interior south-arrow exits activate when walking downward. Tile behavior, collision and elevation determine which movement activates an entrance.

## Example: Littleroot and its houses

Littleroot's north edge connects to Route 101's south edge with no sideways offset. Brendan's front door leads to the separate **Brendan's House 1F** map; that floor has another entrance leading upstairs. Editing the outdoor house artwork changes its appearance. Editing the house interior changes what is inside. Connecting its doorway decides which interior the player enters.

The [Region picture](http://127.0.0.1:8765/region) is the PokéNav illustration and named-location grid. It does not determine walking connections, entrance destinations, or Fly destinations. Configure those alongside the picture when designing a new region.

## Saving and checking

Save pending terrain edits before switching editors. Connection saves update both affected source maps in one backed-up transaction. **Saved history** in the world canvas or terrain editor can restore the files from before a save. Stale forms are rejected if another editor changed the source.

Pokémon definitions remain protected. Source saves are ready for the rebuild workflow; a compiled ROM and gameplay verification still require build-tool and emulator setup. Check travel in both directions, doorway arrival tiles, progression scripts and special travel when playtesting.
