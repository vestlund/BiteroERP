"""Startar MCP-servern (stdio) som Claude-appen ansluter till. Startas av Claude-appen, inte för hand."""

import logging
import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "MCP-server för Claude-appen (stdio). Läggs in i Claude med: manage.py installera_claude"

    def handle(self, *args, **opts):
        # stdout är MCP-protokollet: all loggning måste gå till stderr
        logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
        # Databasen kan behöva uppdateras om programmet uppdaterats sedan senaste start
        from django.core.management import call_command
        call_command("migrate", verbosity=0, stdout=sys.stderr)
        from assistent.server import server
        server.run("stdio")
