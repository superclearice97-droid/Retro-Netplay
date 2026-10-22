#!/bin/sh
# Installs Ubuntu packages for CI. Installs sometimes stalled for an hour, which held up releases. Here: no manual-page
# index rebuild after installing (on these machines it can take that long), a stalled try gives up after a few
# minutes, a half-finished install is repaired before trying again, and later tries use Ubuntu's main archive.
#   sh .github/apt-install.sh package...
set -u
sudo rm -f /var/lib/man-db/auto-update          # skip the slow manual-page index rebuild (CI reads no manuals)
echo 'Acquire::Retries "3"; Acquire::http::Timeout "30"; Acquire::https::Timeout "30";' |
  sudo tee /etc/apt/apt.conf.d/80-ci-retries >/dev/null
for try in 1 2 3; do
  if [ "$try" -gt 1 ]; then
    sudo env DEBIAN_FRONTEND=noninteractive dpkg --configure -a || true    # finish anything the stalled try left
    echo "try $try: switching to Ubuntu's main archive"
    sudo sed -i 's#http://azure.archive.ubuntu.com#http://archive.ubuntu.com#g' \
      /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources 2>/dev/null
  fi
  if timeout 180 sudo apt-get update -qq &&
     timeout 480 sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" >/dev/null; then
    echo "packages installed (try $try)"
    exit 0
  fi
  echo "package install try $try failed or stalled"
  sleep 10
done
echo "couldn't install: $*"
exit 1
