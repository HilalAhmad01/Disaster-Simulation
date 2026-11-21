#!/bin/sh
# Build tippecanoe (felt fork, v2.x) into .tools/tippecanoe without root. Needs git, g++, make, zlib and sqlite3 headers.
set -e
cd "$(dirname "$0")/../../.."
mkdir -p .tools && cd .tools
[ -d tippecanoe ] || git clone --depth 1 https://github.com/felt/tippecanoe.git
cd tippecanoe && make -j"$(nproc)"
./tippecanoe --version
