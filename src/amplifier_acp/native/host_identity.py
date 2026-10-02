"""Extracted native application runtime; provenance in extraction.json."""
import socket


def local_host_identity():
    hostname = socket.gethostname()
    return {'scope': 'local', 'id': hostname, 'label': hostname,
            'identitySource': 'foundation-owner-hostname'}
