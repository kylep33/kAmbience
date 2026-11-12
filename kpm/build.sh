#!/bin/sh
# Assembles the kambience KPM packages into $1 (an empty folder), from the real plugin and sounds.
# Sounds are split in three because GitHub refuses files over 100 MB.
set -e
OUT=$1; HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$HERE/..
[ -n "$OUT" ] || { echo "usage: build.sh OUT_DIR"; exit 1; }
mkdir -p "$OUT/kambience"
cp "$HERE"/kambience/* "$OUT/kambience/"
cp -r "$ROOT/kambience.koplugin" "$OUT/kambience/"
rm -f "$OUT/kambience/kambience.koplugin/test_scenes.py"
part() {  # part N "cat cat ..."
  d="$OUT/kambience-sounds-$1"; mkdir -p "$d/sounds"
  for c in $2; do cp -r "$ROOT/sounds/$c" "$d/sounds/"; done
  cp "$ROOT/sounds/CREDITS.md" "$d/"
  sed -e "s/sounds-N/sounds-$1/; s/part N/part $1/; s|CATS|$(echo $2 | sed 's/ /, /g')|" \
    "$HERE/sounds/manifest.template.json" > "$d/manifest.json"
}
part 1 "battle bells cafe camp cave city field fire forest forge horror"
part 2 "indoor magic night party rain road"
part 3 "scifi sea snow tavern village water wind"
