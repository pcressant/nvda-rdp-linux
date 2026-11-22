"""Code shared by the client and the server: no I/O, no threads, no system library.

* ``framing``: the 32-bit length framing of the local Unix sockets;
* ``rdaccess``: the RDAccess v2 wire format (legacy attribute frames, JSON
  Lines, connection bytes) and its stream decoder;
* ``speech``: the ``NVDA-SPEECH`` vocabulary and NVDA's prosody commands;
* ``braille``: the ``NVDA-BRAILLE`` vocabulary, cells and gestures;
* ``settings``: the JSON encoding of NVDA driver settings.
"""
