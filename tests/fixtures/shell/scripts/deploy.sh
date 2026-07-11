#!/usr/bin/env bash
# deploy script — a comment with an apostrophe: don't let it open a string
set -euo pipefail

source ./lib/common.sh
. "./lib/log.sh"
source "${HELPERS_DIR}/extra.sh"   # variable path -> unresolvable, must be external

VERSION="1.0"          # a literal hash inside a string: "not # a comment"

log_info() {
    echo "[info] $1"
}

function build {
    local out="dist"
    mkdir -p "$out"
    cat <<EOF
    fake() {
      not_a_function
    }
EOF
    echo "built ${out}"
}

deploy() {
    build
    log_info "deploying $VERSION"
}

deploy
