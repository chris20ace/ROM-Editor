# Pokémon Emerald: an editable game atlas

Emerald is built from interconnected systems: a world to explore, scripted events, creatures and combat rules, visual and audio assets, interface screens, and an engine that manages all of them. This workbench lets you rebuild its world and campaign while preserving the original Pokémon species. This atlas connects the editors and game systems to the actual files in this workspace.

All source paths below are relative to **`source/pokeemerald/`**. They have been checked against the downloaded snapshot. The names describe the community decompilation, not original development files recovered from your ROM.

## How the pieces become a game

```text
Maps + story scripts + Pokémon/move/item/trainer tables
                     +
Sprites + palettes + tilesets + music + sound samples
                     +
Engine code for battles, movement, menus, saves, and hardware
                     |
              Compiler and build tools
                     |
                New .gba ROM
                     |
             Emulator or compatible hardware
```

The workbench edits files at the top of this diagram. Building and playing the output are separate stages, and are not set up or verified here.

## Choose an editor

- **[World canvas](/worldmap):** see the outdoor world on one continuous canvas. Zoom, select a town or route, paint terrain or movement, and place objects directly. Save edits to multiple maps together. Interior rooms remain linked separate maps.
- **[Towns & routes](/areas):** open a complete town or route workspace, with its houses, shops, Pokémon Centers, gyms, story buildings and other interiors grouped together. Create blank towns, routes, and interiors here.
- **[Connect places](/connections):** arrange map previews, join walking edges in both directions, or choose both ends of a door or staircase. The PokéNav picture is separate from these travel links. See [Connecting your world](CONNECTING_YOUR_WORLD.md) for examples.
- **[World studio](/world):** draw and edit all 518 original maps. Paint terrain, collision and elevation; add, move or delete NPCs, objects, warps, signs, story triggers and hidden items; resize maps, change settings and connect locations. Start a new blank map with an existing tileset, or clear an existing map.
- **[Region map](/region):** repaint the actual PokéNav/town-map artwork and its named location grid. Edit location names and bounds independently of the terrain the player walks through.
- **[Campaign studio](/campaign):** edit map dialogue and full event scripts, choose the three starters, and configure 854 trainer battles, including 40 gym leader battles and rematches.
- **[Source explorer](/#files):** inspect the game's files and edit advanced story logic, shared scripts, encounters, shops, menus and engine behavior. Pokémon species files, evolutions, learnsets, names and Pokémon graphics are protected from Workbench writes.

Saving updates the local source and backs up previous files. Use **My changes → Export rebuild files (.zip)** when exporting a rebuild: terrain and region artwork include binary files that a text patch does not carry. The export is source changes for this pinned project, not a playable game.

## World and story

| Element | What it controls | Main source locations | Editing approach |
|---|---|---|---|
| Towns, routes, interiors, caves | Map settings, weather, music, map connections | `data/maps/*/map.json`, `data/maps/map_groups.json` | World studio: Map settings, connections, and blank-map creation |
| Ground layout and collision | Which metatiles appear, walkable areas, elevation | `data/layouts/layouts.json`, `data/layouts/*/map.bin`, `data/layouts/*/border.bin` | World studio: Terrain and Movement canvas tools |
| Tilesets and metatiles | Building blocks used to draw maps and give tiles behavior | `data/tilesets/`, `src/data/tilesets/`, `src/metatile_behavior.c` | World studio selects and paints existing tiles; new pixel art needs a palette-aware image editor |
| NPCs, entrances, triggers, hidden items | Object positions, sprites, warp destinations, script labels | Each map's `map.json` | World studio: Objects and Events tools; add, move, edit or delete |
| Dialogue and story events | Conversations, gifts, battles, cutscenes, progression | `data/maps/*/scripts.inc`, `data/scripts/`, `data/text/`, `data/event_scripts.s` | Campaign dialogue forms and full event-script editor; source explorer for shared files |
| Story state | Whether an event happened, quest stages, hidden/shown objects | `include/constants/flags.h`, `include/constants/vars.h`, `src/event_data.c` | Script and code editing |
| Movement and field actions | Walking, cycling, Surf and other field effects, camera | `src/overworld.c`, `src/field_player_avatar.c`, `src/bike.c`, `src/field_control_avatar.c`, `src/fldeff_*.c` | C code editing |
| Region map and healing | PokéNav artwork, named locations, player marker, return/heal points | `graphics/pokenav/`, `src/data/region_map/`, `src/data/heal_locations.json`, `src/region_map.c` | Region map editor for artwork, names and grid; source explorer for healing and special travel rules |
| Weather and time | Visible weather, time events, clock behavior | `src/field_weather.c`, `src/field_weather_effect.c`, `src/time_events.c`, `src/rtc.c` | Map data for selections; C for behavior |

A map and its layout are different objects. A map supplies events and settings; a layout supplies the ground grid. More than one map can reference a layout. Moving an NPC changes map data; drawing a road changes layout data; changing what the NPC says changes a script.

Trees, roofs, walls and most buildings are terrain pieces, assembled from the map's 16×16 metatiles. A building's doorway also needs a warp into an interior. Object sprites are a separate palette for people and movable props. A new blank map is registered in the source but needs a connection or warp so the player can reach it. Existing scripts can refer to object IDs, warp indexes and map locations; update those references when replacing a town or deleting events.

The Region map editor changes the actual town-map picture and location grid. It does not automatically redraw playable routes, reconnect towns, change Fly destinations or rewrite travel scripts. Adjust those parts in their corresponding editors when reshaping the region.

## Pokémon, battles, and balance

| Element | What it controls | Main source locations | Editing approach |
|---|---|---|---|
| Pokémon species | Base stats, types, abilities, catch rates, growth, breeding groups | `src/data/pokemon/species_info.h`, `include/constants/species.h` | Read-only species catalog; definitions are protected |
| Pokémon names and Pokédex | Names, descriptions, size/weight data, ordering | `src/data/text/species_names.h`, `src/data/pokemon/pokedex_entries.h`, `src/data/pokemon/pokedex_text.h`, `src/data/pokemon/pokedex_orders.h` | Protected species data; inspect in source explorer |
| Learned moves | Level-up, TM/HM, egg, and tutor learnsets | `src/data/pokemon/level_up_learnsets.h`, `level_up_learnset_pointers.h`, `tmhm_learnsets.h`, `egg_moves.h`, `tutor_learnsets.h` in the same directory | Protected learnsets; trainer-specific moves are editable in Campaign |
| Evolution and growth | Evolution methods, targets, experience thresholds | `src/data/pokemon/evolution.h`, `src/data/pokemon/experience_tables.h`, `src/pokemon.c`, `src/daycare.c`, `src/evolution_scene.c` | Species tables are protected; source explorer exposes engine implementation |
| Moves | Power, accuracy, PP, type, priority, target, effect selector | `src/data/battle_moves.h`, `include/constants/moves.h`, `src/data/text/move_names.h`, `src/data/text/move_descriptions.h` | Supported numeric forms; source editor for the rest |
| Battle rules and abilities | Turn order, damage, statuses, switching, ability effects | `src/battle_main.c`, `src/battle_util.c`, `src/battle_script_commands.c`, `data/battle_scripts_1.s`, `data/battle_scripts_2.s` | C and battle-script editing |
| Enemy decisions | Move scoring, switching, item use | `data/battle_ai_scripts.s`, `src/battle_ai_script_commands.c`, `src/battle_ai_switch_items.c` | Script and C editing |
| Trainers | Names, classes, portraits, battle settings, team levels and moves | `src/data/trainers.h`, `src/data/trainer_parties.h` | Campaign: 854 trainer records, gym/rematch filters, teams of up to six, held items, moves and AI |
| Wild encounters | Species and level ranges by map and encounter method | `src/data/wild_encounters.json`, `src/wild_encounter.c`, `src/roamer.c`, `src/safari_zone.c` | Source explorer: JSON encounter tables and C for special rules |
| Items and shops | Item properties, prices, descriptions, effects, shop stock | `src/data/items.h`, `src/data/text/item_descriptions.h`, `src/data/pokemon/item_effects.h`, `src/item_use.c`, `src/shop.c`, map scripts | Source editor |
| Choosing starters | Starter selection and its surrounding story | `src/starter_choose.c`, `data/maps/Route101/scripts.inc`, `data/maps/LittlerootTown_ProfessorBirchsLab/scripts.inc` | Campaign: three Birch bag choices; edit rival teams, gifts and story references separately |

Changing a starter, encounter or trainer's party selects an existing Pokémon without changing its species definition. The starter form changes the three Birch bag choices only; rival teams and story references are separate. Each leader battle and rematch is a separate trainer record. Badge awards, gym puzzles, rewards and leader dialogue remain in map scripts.

A move's effect selector points into implemented behavior; typing a new effect name does not create that behavior. Global battle rules are advanced engine edits and can affect the whole game.

## Art, animation, music, and screens

| Element | Main source locations | What you can do here |
|---|---|---|
| Pokémon art | `graphics/pokemon/`, `src/data/pokemon_graphics/`, `src/data/graphics/pokemon.h`, `src/anim_mon_front_pics.c` | Preview existing art; Pokémon graphics and their tables are protected |
| Trainer and overworld sprites | `graphics/trainers/`, `graphics/object_events/`, `src/data/trainer_graphics/`, `src/data/object_events/` | Preview art and edit text tables |
| Battle animation | `graphics/battle_anims/`, `data/battle_anim_scripts.s`, `src/battle_anim*.c` | Inspect assets; edit scripts/code |
| Menus, icons, fonts, title screen | `graphics/interface/`, `graphics/fonts/`, `graphics/items/`, `graphics/title_screen/`, `graphics/text_window/` | Preview graphics; change images with external tools |
| Menu behavior | `src/party_menu.c`, `src/item_menu.c`, `src/start_menu.c`, `src/pokemon_summary_screen.c`, `src/title_screen.c` | Edit C implementation |
| Pokédex, PC storage, PokéNav | `src/pokedex.c`, `src/pokemon_storage_system.c`, `src/pokenav*.c`, related graphics directories | Region editor for PokéNav map artwork; source explorer for system behavior |
| Dialogue rendering | `charmap.txt`, `src/text.c`, `src/strings.c`, `src/text_window.c` | Inspect supported characters and text behavior |
| Music and sound sequences | `sound/songs/midi/`, `sound/song_table.inc`, `include/constants/songs.h` | Inspect filenames; use an external MIDI editor for sequences |
| Instruments and cries | `sound/direct_sound_samples/`, `sound/programmable_wave_samples/`, `sound/voicegroups/`, `sound/cry_tables.inc` | Play WAV samples; inspect sound tables |
| Audio engine | `src/sound.c`, `src/m4a.c`, `src/m4a_1.s`, `sound/MPlayDef.s` | Advanced code/assembly editing |

PNG previews alone do not capture how an asset appears in the game. Sprite dimensions, palette slots, tile order, transparency, and animation tables matter. Some displayed PNGs use preview colors while the game chooses a separate palette. A WAV sample is an ingredient in a song or cry, rather than necessarily the complete sound you hear in-game.

## Side activities and engine services

| System | Main source locations |
|---|---|
| Contests and Pokéblocks | `src/contest*.c`, `src/data/contest_moves.h`, `src/data/contest_opponents.h`, `src/pokeblock*.c`, `src/berry_blender.c` |
| Battle Frontier and Trainer Hill | `src/data/battle_frontier/`, `src/battle_tower.c`, `src/battle_factory.c`, `src/battle_dome.c`, `src/battle_arena.c`, `src/battle_palace.c`, `src/battle_pike.c`, `src/battle_pyramid.c`, `src/trainer_hill.c` |
| Berries and secret bases | `src/berry.c`, `src/secret_base.c`, `src/decoration.c`, `src/data/decoration/` |
| Minigames | `src/slot_machine.c`, `src/roulette.c`, `src/pokemon_jump.c`, `src/dodrio_berry_picking.c`, `src/berry_crush.c` |
| Trading, link play, record mixing | `src/trade.c`, `src/link.c`, `src/link_rfu_*.c`, `src/union_room*.c`, `src/record_mixing.c` |
| Mystery events and gifts | `src/mystery_event*.c`, `src/mystery_gift*.c`, `data/mystery_gift.s` |
| New game and saved state | `src/new_game.c`, `src/save.c`, `src/load_save.c`, `src/save_location.c`, `include/global.h` |
| Engine foundations | `src/main.c`, `src/task.c`, `src/sprite.c`, `src/bg.c`, `src/palette.c`, `src/random.c`, `include/` |
| Compilation and conversion | `Makefile`, `tools/`, `graphics_file_rules.mk`, `audio_rules.mk`, `map_data_rules.mk`, `json_data_rules.mk` |

This covers the main game systems, not a line-by-line explanation of every function. The file inventory lets you inspect the remaining helpers, hardware routines, unused data, and specialized assets. Some auxiliary multiboot programs remain `.gba` binaries under `data/`; the source snapshot is not a promise that every byte has a high-level editor.

## Three small experiments

These are optional exercises. The initial workspace does not apply them. Save one change at a time, reopen the file to confirm it, then either keep it or restore the original value. Workbench saves preserve prior contents under `.workbench/`. Until a build environment exists, verification here means checking source changes, not observing gameplay.

### 1. Draw a new path through Littleroot

Open **[World studio](/world?map=LittlerootTown)** and select Littleroot Town. Right-click an existing path tile to pick it, then choose **Paint** or **Rect** and draw a short path in an open area. Use **Movement** to set the intended walking cells to passable; terrain painting normally keeps existing collision and elevation.

Use **Undo** to review the change, then draw it again if you want to keep it. **Save map**, reopen Littleroot, and confirm the changed terrain. This edits the actual binary layout; no Pokémon data changes. Tile behavior can still restrict movement, so check the path in a compiled game later.

### 2. Start a town on a blank canvas

In World studio, use the **＋** beside the map browser, or **Map → Create a new blank map**. Name it `MyNewTown`, choose a town as its tileset template, and start with a 30×30 grid. Creating the map saves its initial registered source files. Paint your terrain and assemble houses from roof, wall and doorway tiles. Use the Objects palette to place an NPC and Events to add a warp into an existing interior.

Set the return warp and add a connection or entrance from an existing location so the new map is reachable. A new map begins without events or connections. **Map → Clear this map…** is the alternative when you want to rebuild an existing location in place. Use the Region map editor separately when you are ready to represent the town on the PokéNav.

### 3. Give your opening a new conversation

Open **[Campaign studio](/campaign?map=LittlerootTown)**, choose **Story & dialogue**, and select the Littleroot town-sign text block. Replace its text with:

```text
LITTLEROOT TOWN\nA new adventure starts here.$
```

Save, reopen the map's dialogue, and confirm the new message. For an NPC, choose its existing conversation block instead. To give a new NPC dialogue directly, select it in World studio and choose **Write conversation**. Creating the conversation saves its script and links it in the pending map edits; choose **Save map** to finish attaching it.

In Campaign, **New conversation** creates a simple conversation even on a blank map: give it a label and text, then choose **Save conversation to source**. Copy the returned script label into the NPC's **Script to run** field in World studio and save the map. Use **Full event script** for battles, gifts, conditions or cutscenes around the conversation.

Text uses game-specific controls: `\n` starts a line, `\l` scrolls a line, `\p` starts a new page, and `$` ends the message. You can also press Enter in the dialogue forms; it becomes `\n` when saved. Keep placeholders such as `{PLAYER}` where you want the game to insert a name. The simple editor validates supported glyphs and placeholders; advanced formatting belongs in the full script editor. Final text fit and event flow must be tested in a built game.

## Choosing a direction after exploring

- **Your Emerald rebuild:** use World studio for playable land, Region map for the PokéNav view, Campaign for dialogue and battles, and the source explorer for advanced logic. Set up the matching build tools to turn the edits into a playable ROM.
- **An independent game:** use the atlas to decide which systems you want, then build those in your chosen engine with suitable art, audio, and writing.
- **A small learning project:** start with one town, a starter choice, one trainer and a short story loop before rebuilding the entire region.

The next technical checkpoint for an Emerald modification is an unchanged build that passes `make compare`. After deliberate game changes, the original checksum is expected to differ. Compile and play-test those changes instead of expecting the modified game to match the original checksum.

## Maintainer references

- [pret/pokeemerald source and target ROM checksum](https://github.com/pret/pokeemerald)
- [Pinned source snapshot used here](https://github.com/pret/pokeemerald/tree/5eff78649e7170a877b961ef0b3da13b81a16038)
- [Build requirements and instructions](https://github.com/pret/pokeemerald/blob/master/INSTALL.md)

Use the build instructions shipped in this pinned source revision when configuring the compiler and tools. No successful baseline ROM build or emulator session has been verified in this workspace yet.
