#!/bin/sh
set -eu
# Disables only this pilot job. It does not delete company evidence or user files.
launchctl bootout "gui/$(id -u)/com.company-pilot.desktop"
