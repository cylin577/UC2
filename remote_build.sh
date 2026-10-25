#!/bin/bash
# Remote build script for UC2 calibration client
# Usage: ./remote_build.sh [user@host] [remote_path]

set -e

# Configuration (Edit these or pass as arguments)
REMOTE_HOST=${1:-"build-machine"}
REMOTE_PATH=${2:-"~/UC2_build"}

LOCAL_BUILD_DIR="remote_build"
CLIENT_DIR="calibration_client"
RES_DIR="resources"
APP_NAME="uc2_calib"

echo "Syncing source and resources to $REMOTE_HOST:$REMOTE_PATH..."

# Create remote directory structure
ssh "$REMOTE_HOST" "mkdir -p $REMOTE_PATH/$CLIENT_DIR $REMOTE_PATH/$RES_DIR"

# Sync calibration client (source, Makefile, its own resources if any)
rsync -avz --delete \
    --exclude "build/" \
    "$CLIENT_DIR/" \
    "$REMOTE_HOST:$REMOTE_PATH/$CLIENT_DIR/"

# Sync top-level resources directory
rsync -avz --delete \
    "$RES_DIR/" \
    "$REMOTE_HOST:$REMOTE_PATH/$RES_DIR/"

echo "Executing remote build..."
ssh "$REMOTE_HOST" "cd $REMOTE_PATH/$CLIENT_DIR && make 3dsx cia"

echo "Retrieving artifacts..."
mkdir -p "$LOCAL_BUILD_DIR"
rsync -avz "$REMOTE_HOST:$REMOTE_PATH/$CLIENT_DIR/$APP_NAME.3dsx" "$LOCAL_BUILD_DIR/"
rsync -avz "$REMOTE_HOST:$REMOTE_PATH/$CLIENT_DIR/$APP_NAME.cia" "$LOCAL_BUILD_DIR/"
rsync -avz "$REMOTE_HOST:$REMOTE_PATH/$CLIENT_DIR/build/$APP_NAME.elf" "$LOCAL_BUILD_DIR/"

echo "Build complete. Artifacts in $LOCAL_BUILD_DIR/"