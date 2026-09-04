MINIONS IN 32K — PLAYER QUICK START
===================================

Windows PowerShell:
  .\minions-in-32k.exe --server http://TARGET_IP:8080

Linux:
  chmod +x minions-in-32k
  ./minions-in-32k --server http://TARGET_IP:8080

Connect the event VPN, then replace TARGET_IP with the current private target
shown by RSCTF. Copy the stable KotH token from the challenge panel, paste it
into the masked SECURE EVENT LOBBY field, then select CONNECT. The target can
change after a Crown reset, so restart with the address currently shown by
RSCTF. The --token option remains available for scripted launchers.

The lobby exchanges the stable RSCTF KotH capability with the private managed
target, then connects to the same target and port over certificate-pinned
QUIC/UDP. Plain HTTP is accepted only for a private IPv4 address.

Choose a minion during the 30-second staging period. Move with W/A/S/D, aim
with the mouse, and fire with the left mouse button. An opponent frag drops a
server-owned banana for 12 seconds. Collect your yellow banana to CONFIRM the
kill; after you respawn, recover your red banana to DENY the killer. Other
players cannot steal either objective. Confirmed kills decide the result;
total frags and then fewer deaths break ties.

Stay connected through the final standings. The client no longer closes when
the match ends: use RETURN TO SECURE LOBBY to play again with the same saved
KotH token. A fresh short-lived arena key is still issued for every match.

Linux runtime packages vary by distribution. The client needs a Vulkan-capable
graphics driver, ALSA, libudev, and X11 compatibility libraries. The Windows
client is the recommended build for the current private event.

The archive includes the AGPL-3.0 code license, the CC BY-NC-SA 4.0 content
license, and the full attribution/modification notice.
