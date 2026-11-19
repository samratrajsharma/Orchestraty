"""Team sync: teammates' agents made aware of each other's work through git.

These are the building blocks the gates and the commands are made of: a small reader for the
YAML the repository's people and policy files are written in (``miniyaml``), the grammar every
field of a shared record must follow (``grammar``), the people file and CODEOWNERS (``people``,
``codeowners``), who this machine shares as (``identity``), what the repository's policy allows
(``policy``) and the secret scanner (``scan``).

No module here opens a socket: what reaches another machine goes through git, run as a
process of its own. None of this runs for a user who has not turned team sync on.
"""
