"""Historical prototype import retained for its validation tools.

The verified implementation now lives in the production UI package. The
application never imports this script; both old and new checks exercise the
same layout implementation rather than maintaining two copies.
"""

from ui.prose_edit import ProseTextEdit as SpacedProseEdit, SpacedPlainTextLayout
