#!/bin/sh
# KPM runs this from the package folder after unpacking. KOReader loads the plugin on its next start.
PLUGINS=/mnt/us/koreader/plugins
[ -d "$PLUGINS" ] || { echo "KOReader not found at /mnt/us/koreader"; exit 1; }
rm -rf "$PLUGINS/kambience.koplugin"
cp -r kambience.koplugin "$PLUGINS/"
[ -x /mnt/us/KinAMP/startkinamp_koreader.sh ] || echo "kambience needs KinAMP: install it to /mnt/us/KinAMP"
echo "kambience installed. Restart KOReader to load it."
