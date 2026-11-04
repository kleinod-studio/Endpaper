#!/usr/bin/env bash
# Starts Endpaper straight from the source folder, without Meson or Flatpak (for development only).
cd "$(dirname "$0")" || exit 1

# Compile the translations (locale/ is not checked in)
if command -v msgfmt >/dev/null; then
  for po in po/*.po; do
    [ -e "$po" ] || continue
    lang=$(basename "$po" .po)
    mkdir -p "locale/$lang/LC_MESSAGES"
    msgfmt -o "locale/$lang/LC_MESSAGES/endpaper.mo" "$po" 2>/dev/null
  done
fi

# GTK's warnings about frame timing are harmless and only visible here, so they are filtered out;
# all other messages still show.
PYTHONPATH=src exec python3 -m endpaper "$@" 2> >(grep --line-buffered -v -e 'gdk_frame_timings' -e '^$' >&2)
