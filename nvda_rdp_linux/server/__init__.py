"""Server role: Orca, in a GNOME session reached over RDP, speaking and
showing braille through NVDA on the RDP client machine.

* ``channel``: the server side of one RDAccess channel, on the GLib loop,
  with the session's time since input for both channels (``input_time``);
* ``speechd_module``: a Speech Dispatcher output module that speaks through
  NVDA, with per-utterance prosody (``prosody``, ``orca_speech``);
* ``brlapi_server``: a BrlAPI server that offers the client's braille display
  to Orca (``braille_bridge``, ``orca_braille``), with display keys and
  braille typing (``braille_input``, ``mutter_input``).

Imported from the study draft-nvda-orca-rdp at faae513 (experiments 03 to
13), then ported onto ``common``.
"""
