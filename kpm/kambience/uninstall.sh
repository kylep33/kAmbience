#!/bin/sh
# On upgrade KPM reinstalls right after, so keep the plugin.
[ "$1" = "upgrade" ] && exit 0
rm -rf /mnt/us/koreader/plugins/kambience.koplugin
