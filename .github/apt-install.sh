#!/bin/sh
# Installs Ubuntu packages for CI. The runners' usual mirror (azure.archive.ubuntu.com) sometimes stalls for an hour,
# which held up releases: here a stalled try gives up after a few minutes, and later tries use Ubuntu's main
# archive instead.
#   sh .github/apt-install.sh package...
set -u
echo 'Acquire::Retries "3"; Acquire::http::Timeout "30"; Acquire::https::Timeout "30";' |
  sudo tee /etc/apt/apt.conf.d/80-ci-retries >/dev/null
for try in 1 2 3; do
  if [ "$try" -gt 1 ]; then
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
