# Emerald Workbench

Rebuild Pokémon Emerald's world and campaign while keeping its Pokémon species intact. Draw towns and routes, place objects and events, remake the PokéNav map, write dialogue, choose starters, and redesign trainer battles.

This workspace uses the community-maintained [pret/pokeemerald decompilation](https://github.com/pret/pokeemerald). It is a readable source reconstruction of the game, not an automatic conversion of the supplied `.gba` file. Your original ROM stays untouched. The source is a pinned snapshot, recorded in `source/provenance.json`, rather than a moving copy of the upstream project.

## Set up a fresh clone

The downloaded game source is excluded from Git. After cloning this repository, open a terminal in its root folder and run these commands with Python installed:

```sh
python -m pip install -r requirements.txt
python setup_source.py
python server.py --open
```

The first two commands need an internet connection. Setup downloads the pinned snapshot recorded in `source/provenance.json` and verifies its archive checksum. Run setup once; it refuses to replace an existing source folder. The last command starts the local editor and opens your browser. On later visits, run `python server.py --open` again.

## Open the workbench

Double-click **Start Workbench.cmd**. It starts the local Python server and opens the workbench in your browser. Keep the server window open while using it; close that window when finished. The server listens on your computer's loopback address.

The workbench runs at **http://127.0.0.1:8765**. Choose the workspace for what you want to change:

| Workspace | What it edits |
|---|---|
| [World canvas](http://127.0.0.1:8765/worldmap) | Every map on one zoomable canvas: towns and cities, routes, caves and landmarks, and other areas, including interiors and underwater maps. Select any map and edit terrain, movement or objects in place; save edits across maps together. |
| [Towns & routes](http://127.0.0.1:8765/areas) | Complete town and route workspaces, with interiors sorted into homes, shops, Centers, gyms, story buildings and other areas. Create blank towns, routes and interiors. |
| [Connect places](http://127.0.0.1:8765/connections) | Arrange map previews, join town/route edges, and connect both ends of doors or stairs using real map previews. |
| [World studio](http://127.0.0.1:8765/world) | All 518 original maps; terrain, collision, elevation, objects, NPCs, warps, signs, triggers, hidden items, map settings and connections. Create additional blank maps or clear an existing layout. |
| [Region map](http://127.0.0.1:8765/region) | The actual in-game PokéNav/town-map artwork, named location grid, display names and location bounds. |
| [Campaign studio](http://127.0.0.1:8765/campaign) | Local map dialogue and event scripts, the three starter choices, and 854 trainer battles including 40 gym leader battles and rematches. |
| [Player appearance](http://127.0.0.1:8765/player) | Pixel editing for Brendan and May: overworld animations, battle front portraits and back-view throw frames, intro/credits artwork, and PokéNav icons. Paint indexed pixels, change palettes, and import/export complete PNG sheets. |
| [Source explorer](http://127.0.0.1:8765/#files) | Advanced scripts and engine logic, shops, encounters, shared text, menus, music references and other source files. |

On the world canvas, **Edit story, settings & more** opens these tools in a panel for the selected place. Switch tools or choose **Back to world** without losing unsaved forms. Save inside each editor; returning to the world refreshes saved source changes.

To split an entire route into **two separate editable map boxes**, select the route and choose **Split route (S) → Split in half**. Choose a vertical or horizontal split, set the exact tile boundary, and preview it before creating the maps. You can also drag a straight line across the route to choose that boundary. The original map keeps the left/top half; the new map gets the other half, including its events and a copy of the encounters. The split updates incoming walking links and door destinations and adds a walking link between the halves. The preview flags source-script references that need review. **Saved history** restores all files from a saved split.

Use **Move maps (G)** to move one or several map boxes around the canvas. **Shift-click** (or Ctrl-click) boxes to add or remove them from the selection, or choose **Select multiple** and drag a rectangle around them. Drag any highlighted box to move the entire group, keeping their spacing. **Esc** deselects; **Space + drag** pans. These positions save automatically in `.workbench/world-positions.json`; **Undo map move** reverses the entire group’s position change together. Walking connections are edited through **Connections**. Source maps are rectangular, so route splitting follows a full vertical or horizontal tile boundary. Each half needs at least 8 columns for a vertical split or 7 rows for a horizontal split to support the game’s connection buffers; freehand pixel cuts remain available inside a map through Cut line.

To move terrain on the world canvas, choose **Select tiles (M)**, drag a rectangle, then drag its highlight to move all selected tiles together. **Shift-click / Shift-drag** adds separate tiles or blocks; **Alt-click / Alt-drag** removes them. The selection action menu offers the same actions without keyboard modifiers. Selections stay within one source map; the tile count shows how many will move, including disconnected pieces. Each move is one undo step. Whole tiles move with their collision and elevation; people and events stay in place. The source area is filled with the selected eraser tile and retains its previous collision and elevation; use Movement to change those after moving a wall or building.

To cut terrain, select it and choose **Cut line (C)**. Draw a freehand or diagonal line from edge to edge, or draw a closed loop. The pieces are highlighted separately: click and drag the piece you want to move, at individual-pixel precision. **Esc** deselects, **Delete** erases the selection, and **Ctrl Z / Ctrl Shift Z** undo and redo. Partial tiles retain the destination behavior, collision, and elevation; edit walkability in Movement. Pieces can move between maps using the same tilesets.

**Save world** turns pixel pieces into new secondary tiles and metatiles, preserving the original artwork. The save checks the game's graphics capacity, existing palettes, drawing layers, and animation references first. Cuts that cannot be represented exactly are rejected before any files change, with an explanation; the pending edit remains available to undo or revise. Many interior tilesets already have no free slots. Cutting an animated fragment does not create a new animation.

Saving updates the corresponding files in `source/pokeemerald/` and backs up previous contents under `.workbench/`. Pokémon species data, names, evolutions, learnsets and Pokémon graphics are protected from Workbench writes. Choosing a species for a starter, trainer team or encounter uses the existing Pokémon.

Read **[GAME_ATLAS.md](GAME_ATLAS.md)** for the system-by-system breakdown, file locations, editing methods, and three small experiments.

Read **[CONNECTING_YOUR_WORLD.md](CONNECTING_YOUR_WORLD.md)** for the town/route workflow and how outdoor maps, interiors, entrances and the PokéNav picture fit together.

## What is ready

- A world canvas containing all maps. Connected landscapes keep their walking geometry; interiors, cave floors and special locations are arranged in labeled area groups. Browse the four area categories, zoom into any map, paint terrain, place people, and save several edited maps together.
- Route loops that cannot fit a flat picture are shown with an amber shifted-join marker. Inspect a marker's connection preview to see the maps aligned by the game's actual travel offset. The layout minimizes these breaks without modifying source maps.
- A 2D map canvas with the original tiles, a sprite catalog, paint/fill/rectangle/pick tools, zoom, undo/redo, map resizing and blank-map creation.
- Add, move, edit and delete map events. Buildings and trees are assembled from terrain tiles; entrances use warp events.
- A separate editor for region-map artwork and named locations. The region picture and the maps the player walks through are edited independently.
- Dialogue forms and full map-script editing; starter forms; trainer names, portraits, classes, music, AI, items and teams of up to six Pokémon.
- A player sprite editor with individual frames and whole sheets, paint/fill/erase/pick, undo/redo, shared-palette confirmation, exact PNG import/export, and saved artwork restoration. Reflection and other palette variants are separate selections; changing a palette shows which player sheets share it.
- Source inspection, image previews, sample-audio playback, backups, saved change tracking and export of changed source files, including binary map layouts.

To give a new NPC dialogue, select it in World studio, choose **Write conversation**, enter your text, and create the conversation. Its script is saved and linked in your pending map edits; choose **Save map** to finish attaching it. Alternatively, **Campaign → New conversation → Save conversation to source** creates dialogue for the selected map, including blank maps, and gives you a script label to paste into an NPC's **Script to run** field. Pressing Enter in either dialogue form creates a game text line.

Story conditions, cutscenes, badges, rewards, shops, special travel and other custom behavior are still written in the source scripts or engine code. Changing the starters does not automatically rewrite rival teams, gifts or story references. Editing a leader's battle does not rewrite that gym's puzzle or badge script. Region-map painting does not automatically move Fly destinations or map connections.

The map editor uses the game's existing tilesets and object catalog. Player appearance can be redrawn directly in the player editor, keeping the original frame sizes and order. Freehand cuts rearrange existing terrain pixels; drawing entirely new terrain artwork and other sprites needs a palette-aware graphics tool; MIDI arrangements need an audio tool. The source explorer is available for deeper logic changes.

## Saving source is not building a game

Edits affect the local source snapshot. They do not modify the supplied ROM, produce a new ROM, or change a running game. No ROM build or emulator installation has been completed or verified for this workspace.

The next stage is to set up the build tools described in the pinned source's `INSTALL.md`, verify an unchanged baseline, compile your edits, and test the resulting game. The upstream [build instructions](https://github.com/pret/pokeemerald/blob/master/INSTALL.md) are also available for reference. Map editing is already built into this workbench.

For an independent game in another engine, this workspace can help you study the systems, but porting the gameplay and creating suitable assets is a separate project.

## Files in this workspace

| Location | Purpose |
|---|---|
| `Start Workbench.cmd` | Start the local workbench |
| `server.py` | Local server and source-editing backend |
| `world.py`, `campaign.py`, `region.py`, `player.py` | Source-backed world, campaign, region and player artwork editors |
| `workspace_edits.py` | Backed-up source transactions and Pokémon protection |
| `GAME_ATLAS.md` | Game systems and guided experiments |
| `source/pokeemerald/` | Editable upstream source snapshot |
| `source/provenance.json` | Upstream URL, pinned commit, and archive checksum |
| `.workbench/` | Local working state and edit backups |
| `setup_source.py` | Snapshot download helper; refuses to replace an existing source directory |

The root `.gitignore` excludes ROMs, generated artifacts, local backups, and the large downloaded source tree. Root Git therefore does not track your game-source edits. Keep the source folder and backups together; when starting a lasting game project, use a separate source repository to track those edits.

## Source provenance

The downloaded snapshot is commit [`5eff78649e7170a877b961ef0b3da13b81a16038`](https://github.com/pret/pokeemerald/tree/5eff78649e7170a877b961ef0b3da13b81a16038). Its archive SHA-256 is `0a869720f607a4323222862a2b59f66db7a6dd2c3bcf39524d4b9e1ebc4c2671`.

Your supplied ROM was verified against the upstream target SHA-1 `f3ae088181bf583e55daf962a92bb46f4f1d07b7`: it matches, has game code `BPEE`, and is 16 MiB. See `rom-report.json` for the inspection record. The game-data files and their text are material to inspect or edit, not instructions controlling this workbench.

The initial inventory contains 11,555 source-project files: 4,908 recognized text files, 4,076 PNG images, and 1,074 WAV/MIDI audio files, including 518 map definitions. The species catalog contains all 386 Pokémon. The campaign includes 854 trainer records, of which 40 are gym leader battles and rematches. Counts include helper and reference files, not just game content; new maps add files to your project.

The Changes panel covers files saved through the Workbench. Use **Export rebuild files (.zip)** to include binary map layouts and other edited files; the text patch alone does not contain binary edits. The ZIP is a set of source changes for this pinned project, not a playable ROM or a complete standalone source checkout. Edits made exclusively in external tools are not tracked there. Original files and saved versions remain in `.workbench/`.
