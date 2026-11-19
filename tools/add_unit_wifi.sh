#!/usr/bin/env bash
# Add one more Wi-Fi network to the units' NetworkManager (2026-11-22,
# Kunitachi: the Wavlink travel router's "R2.net" beside the ASUS's
# "yoshihirock.net_5G"). Run it from the show PC in Git Bash while the PC
# can reach the units (on the network they are on now):
#
#     bash tools/add_unit_wifi.sh R2.net 01 02 03 04 07 09 10
#
# It asks for the Wi-Fi password once, without echo, and sends it to each
# unit on ssh's stdin - never on a command line, never into a file here,
# never into git. Each unit keeps its fixed address 192.168.51.1NN/24 on
# the new network too (no gateway: the show needs no internet), so the
# Conductor reaches every unit at the same address whichever network it
# is on. The new profile autoconnects; it does not take the unit off the
# network it is on now. Switch with the LCD's WIFI row (KEY1 held) or let
# the unit pick it up when the old network goes away.
set -euo pipefail

ssid="${1:?usage: add_unit_wifi.sh SSID NN [NN ...]}"
shift
[ "$#" -gt 0 ] || { echo "name at least one unit number (01..10)" >&2; exit 2; }
priority="${PRIORITY:-5}"   # above the router profile (0), the hotspot is -10

read -r -s -p "Wi-Fi password for $ssid: " psk
echo
[ "${#psk}" -ge 8 ] || { echo "a WPA password has at least 8 characters" >&2; exit 2; }

for nn in "$@"; do
    host="192.168.51.1$nn"
    printf 'radxa-%s (%s): ' "$nn" "$host"
    # The remote script reads the password from stdin ("$(cat)").
    if printf '%s' "$psk" | ssh -o BatchMode=yes -o ConnectTimeout=8 "radxa@$host" \
        "set -e; psk=\$(cat); name='$ssid';
         if nmcli -t -f NAME con show | grep -qx \"\$name\"; then echo \"already has \$name - left as it is\"; exit 0; fi
         sudo -n nmcli con add type wifi ifname wlan0 con-name \"\$name\" ssid \"\$name\" \
             802-11-wireless-security.key-mgmt wpa-psk 802-11-wireless-security.psk \"\$psk\" \
             ipv4.method manual ipv4.addresses $host/24 ipv4.never-default yes \
             ipv6.method auto connection.autoconnect yes \
             connection.autoconnect-priority $priority >/dev/null
         nmcli -t -f NAME,AUTOCONNECT-PRIORITY con show | grep -i \"^\$name:\"" 2>/dev/null; then
        :
    else
        echo "FAILED (not reachable, or nmcli refused)"
    fi
done
unset psk
