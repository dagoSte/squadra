"""Private collector of the memory candidates that squadra agents declare, with a propose-only connector to
memoranda.dev. It never approves; only `send` proposes."""
import sys

sys.dont_write_bytecode = True

VERSION = "1.0.3"
FORMAT = "squadra-memory-candidates"
FORMAT_VERSION = 1
DECLARATION_FORM = "da-ricordare/1"
DECLARATION_NAME = "da-ricordare.json"
LISTS = ("candidates", "contested", "withheld", "evidence_not_found", "malformed", "unauthorized", "dropped")
