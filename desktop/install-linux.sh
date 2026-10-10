#!/bin/sh
# Adds "Netplay Matchmaker" to your app menu.
DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$DIR/start.sh"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/netplay-matchmaker.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Netplay Matchmaker
Comment=Find an opponent for retro games
Exec=$DIR/start.sh %u
Path=$DIR
Terminal=false
Categories=Game;
MimeType=x-scheme-handler/netplay;
DESK
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null
echo "Done. Search for 'Netplay Matchmaker' in your app menu."
echo "(If it doesn't open, run $DIR/start.sh in a terminal to see why.)"
