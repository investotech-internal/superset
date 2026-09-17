#!/bin/bash
# Install system dependencies required for Superset development and production
# This script ensures all build dependencies and runtime libraries are available.
#
# It checks which required packages are already installed and only invokes
# `sudo apt-get` (which prompts for a password) when something is actually
# missing. On machines where everything is already present, this script runs
# with no sudo prompt at all.

set -e

REQUIRED_PACKAGES=(
    libmariadb-dev
    libldap2-dev
    libsasl2-dev
    libpq-dev
    python3-dev
    python3-full
    build-essential
    pkg-config
)

MISSING_PACKAGES=()
for pkg in "${REQUIRED_PACKAGES[@]}"; do
    if ! dpkg -s "$pkg" &> /dev/null; then
        MISSING_PACKAGES+=("$pkg")
    fi
done

NEED_NODE=false
if ! command -v node &> /dev/null; then
    # Try sourcing nvm in case node/npm are managed by nvm but this script is
    # running in a non-interactive shell (e.g. invoked from `make`) that
    # hasn't sourced ~/.bashrc.
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [ -s "$NVM_DIR/nvm.sh" ]; then
        # shellcheck disable=SC1091
        \. "$NVM_DIR/nvm.sh"
    fi
fi

if ! command -v node &> /dev/null; then
    NEED_NODE=true
    echo "⚠️  node/npm not found. This project expects Node.js managed via nvm."
    echo "   Install nvm (https://github.com/nvm-sh/nvm) and run 'nvm install --lts',"
    echo "   or install Node.js manually, then re-run this script."
fi

if [ ${#MISSING_PACKAGES[@]} -eq 0 ]; then
    echo "✅ All system dependencies already installed, skipping apt (no sudo needed)."
    exit 0
fi

echo "Installing missing system dependencies: ${MISSING_PACKAGES[*]}"
sudo apt-get update -qq
sudo apt-get install -y -qq "${MISSING_PACKAGES[@]}"

echo "✅ System dependencies installed successfully!"
