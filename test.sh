#!/usr/bin/env bash
# Kör de automatiska testerna. Din riktiga databas rörs inte.
cd "$(dirname "$0")"
uv run python manage.py test tester "$@"
