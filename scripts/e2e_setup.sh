#!/usr/bin/env bash
# Provision a headless Chromium for `npm run e2e` on a machine without a system
# browser (e.g. WSL2, no sudo). Idempotent — safe to re-run. Run once:
#
#   npm run e2e:setup
#
# Installs:
#   1. Chromium into ~/.cache/puppeteer (via @puppeteer/browsers)
#   2. The few runtime libs Chromium needs that minimal WSL/Ubuntu images omit
#      (libnspr4 / libnss3 / libasound2t64), fetched WITHOUT sudo via
#      `apt-get download` and extracted to ~/.cache/ct3d-e2e/libs. The test
#      points Chromium's LD_LIBRARY_PATH at this dir.
set -euo pipefail

CACHE="${HOME}/.cache/ct3d-e2e"
LIBS="${CACHE}/libs"
mkdir -p "${CACHE}/debs" "${LIBS}"

echo "• installing Chromium (puppeteer browsers)…"
npx --yes puppeteer@23 browsers install chrome >/dev/null

if [ -f "${LIBS}/libnss3.so" ]; then
  echo "• runtime libs already extracted (${LIBS})"
else
  echo "• fetching Chromium runtime libs (no sudo)…"
  cd "${CACHE}/debs"
  apt-get download libnspr4 libnss3 libasound2t64
  for d in *.deb; do dpkg-deb -x "$d" "${CACHE}/root"; done
  cp -a "${CACHE}/root/usr/lib/x86_64-linux-gnu/." "${LIBS}/"
fi

echo "ok ✓  chrome + libs ready — run: npm run e2e"
