RYTHME CROWN CLIENT
===================

Rythme.exe is a Windows x86-64 fork built from the complete osu!lazer source
tree. It opens on lazer's normal start menu and keeps the default Argon skin,
settings, audio controls, input bindings, editor, local library, and solo play.
The single executable contains its required native Windows policy library;
the .NET host extracts that component automatically when the client starts.

Open PLAY, MULTI, then CROWN ARENA. The event coordinator is preconfigured as
https://rythme-private.tcp.1pc.tf/. Paste only the `koth_...` token shown in
the RSCTF challenge panel. RSCTF supplies your official team identity; no
server URL, Team ID, or crew name is needed.

The event client has no local Team ID or crew-name fields. It exchanges the
RSCTF KotH token for the authoritative team identity. The token lasts for the
event unless your team explicitly rotates it after exposure; reconnect with
the replacement after a rotation. One session is the performer; additional
sessions spectate.

SCORING WAVES
-------------

1. After login, wait for EVENT POOL READY. The client downloads all 21 pinned
   archives directly from the pinned HTTPS mirror, verifies every SHA-256, and
   keeps them in the local Rythme cache. The coordinator is an automatic
   fallback. Later launches reuse valid files and repair only missing or
   damaged archives.
2. Use MODE (or left/right) to choose osu!, taiko, catch, or mania. Select
   EDIT MODS or press F1 to open lazer's normal Mod Select and save one
   compatible native loadout for that mode. All four start at NM. The lobby
   keeps the four choices across waves. Select ARM AUTO START once; the client
   enrolls at the next opening and applies only the revealed mode's loadout.
   Select the main button again at any time in the lobby to disarm it.
3. The reveal selects one exact community beatmap difficulty from the
   organizer's supplied song list. The mode order is random, but every rotation
   contains osu!, taiko, catch, and 4K mania exactly once.
4. The lobby reads the selected .osz from the verified cache, checks its exact
   beatmap ID and .osu content identity, and imports it into the local lazer
   library. A stale local chart with the same online ID is never reused. Select
   the download icon if that cached archive ever needs repair.
5. AUTO START applies the saved loadout for the revealed mode. For a manual
   change, disarm AUTO START after reveal, select EDIT MODS or press F1, close
   the normal lazer Mod Select overlay, then select LOCK MODS & READY.
6. Every ready player starts the same chart from the same server time.
7. Every completed wave produces an official score. One unique best positive
   score takes the Crown; an exact top-score tie awards no Crown to either team.
   Complete future waves to improve your relative score and defend the Crown.

The current pool contains 21 real community difficulties. It does not contain
Rythme-generated charts. The selected difficulties range from 3.58 to 7.29
stars. Mania selections are native 4K maps. The verified archive cache uses
about 284 MiB under `%LOCALAPPDATA%\Rythme\BeatmapCache\v2`.

LIVE PLAY
---------

The coordinator judges fresh input receipts. A score declaration by itself is
not accepted. Each successful lazer judgment is tied to a short-lived server
cue for the exact hit object. Those thresholds and the divergent modifier proof
qualify the exploit boost. A lower completed run still receives an official
native score; the boost makes that score much more competitive.

Controls:

  osu!      Move with the mouse; Z/X hit.
  taiko     D/K hit the rim; F/J hit the centre.
  catch     Left/right or Z/X move; Shift dashes.
  mania     D/F/J/K are the four lanes.

MODS
----

Rythme uses the pinned ruleset's native ScoreMultiplierCalculator. Mod Select
shows the combined playback rate and score multiplier. Ready-up sends the
selected acronyms; the coordinator validates their compatibility and returns
the locked profile. Unknown, incompatible, automation, and variable-rate
combinations are rejected.

Compatible fixed-rate mods can be combined as in upstream lazer, and each
ruleset keeps its own saved loadout for future waves. Custom mod settings are
disabled because every performer shares one start clock.

STARTUP FAILURES
----------------

If the client cannot create its first window, it displays a failure dialog with
an error ID and log path. Logs are normally written under:

  %LOCALAPPDATA%\Rythme\Logs

The system temporary directory is used as a fallback. The report contains
runtime and exception details. It does not record command-line arguments, team
tokens, or arena credentials.

NETWORK AND ATTRIBUTION
-----------------------

The client does not sign in to Spotify or the official osu! service. It fetches
the public beatmap archives directly from the pool's pinned `catboy.best` HTTPS
URLs so the event host does not relay roughly 284 MiB to every player. That
mirror can observe the player's source IP. The client uses a separate HTTP
connection for it and never sends the RSCTF token, Rythme bearer, cookies, or
team identity to the mirror. Redirects and any other host are rejected. An
archive is accepted only when its SHA-256 matches the checksum embedded in the
signed client; otherwise the coordinator serves its verified organizer copy as
the automatic fallback. No Spotify or osu! OAuth token is required. Official
online features such as Lounge and Ranked Play are disabled in this fork;
offline lazer functionality remains available.

Rythme is an independent CTF challenge and is not affiliated with or endorsed
by ppy. See THIRD-PARTY-NOTICES.txt for software, resource, beatmap, and music
attribution.
