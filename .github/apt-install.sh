#!/bin/sh
# Installs Ubuntu packages for CI. Ubuntu's mirrors sometimes stall a download for an hour, which held up releases:
# here a stalled step gives up after a few minutes and tries again (three times).
#   sh .github/apt-install.sh package...
set -u
echo 'Acquire::Retries "3"; Acquire::http::Timeout "30"; Acquire::https::Timeout "30";' |
  sudo tee /etc/apt/apt.conf.d/80-ci-retries >/dev/null
for try in 1 2 3; do
  if timeout 300 sudo apt-get update -qq &&
     timeout 900 sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" >/dev/null; then
    exit 0
  fi
  echo "package install try $try failed or stalled; trying again"
  sleep 15
done
echo "couldn't install: $*"
exit 1
