"""Author structured style requirements in temporary test projects."""
from core.style_library import empty_library, save_library, revision, PROFILE_FIELDS

def set_style(root, text):
    if len(text) > 600 * len(PROFILE_FIELDS):
        raise ValueError('Test requirements exceed the supported fields')
    value = empty_library()
    value['profile'] = {name: text[i*600:(i+1)*600] for i, name in enumerate(PROFILE_FIELDS) if text[i*600:(i+1)*600]}
    return save_library(root, value, expected_revision=revision(root))
