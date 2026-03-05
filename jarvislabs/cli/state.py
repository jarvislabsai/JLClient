# Global CLI state — set by app.py callback, read by commands.
# Separate module to avoid circular imports between app.py and command modules.

json_output: bool = False
yes: bool = False
token: str | None = None
