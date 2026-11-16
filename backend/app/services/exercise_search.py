"""Display/search metadata only. Callers retain ownership, health and active-state filtering."""
import re
import unicodedata
from app.services.exercise_catalog_v1 import catalog_entries

BODY_PARTS = ('胸部', '背部', '肩部', '手臂', '臀部', '腿部', '核心')
MUSCLES = {
    'chest': '胸部', '胸': '胸部', '胸肌': '胸部', 'back': '背部', '背': '背部', '背阔肌': '背部',
    'front_deltoid': '肩部', 'side_deltoid': '肩部', 'rear_deltoid': '肩部', 'shoulders': '肩部', '肩': '肩部',
    'biceps': '手臂', 'triceps': '手臂', 'forearms': '手臂', '肱二头肌': '手臂', '肱三头肌': '手臂',
    'glutes': '臀部', '臀': '臀部', '臀肌': '臀部', 'quads': '腿部', 'hamstrings': '腿部',
    'calves': '腿部', 'adductors': '腿部', '腿': '腿部', '股四头肌': '腿部', '腘绳肌': '腿部', '小腿': '腿部',
    'core': '核心', 'abs': '核心', '腹肌': '核心', '腹部': '核心',
}


def selection_metadata(exercise):
    entry = next((row for row in catalog_entries() if getattr(exercise, 'owner_id', None) is None
                  and row['fields']['name_zh'] == exercise.name_zh
                  and row['fields']['name_en'].casefold() == exercise.name_en.casefold()), None)
    if entry:
        return dict(body_parts=list(entry['body_parts']), search_aliases=list(entry['aliases']),
                    counting_note=entry['counting_note'])
    muscles = list(getattr(exercise, 'muscle_primary', None) or []) + list(getattr(exercise, 'muscle_secondary', None) or [])
    parts = list(dict.fromkeys(MUSCLES.get(str(value), str(value)) for value in muscles))
    # Unknown private classifications stay visible under All; never guess by name.
    return dict(body_parts=[value for value in parts if value in BODY_PARTS], search_aliases=[], counting_note=None)


def normalize_query(value):
    return re.sub(r'[\s\-·()（）]', '', unicodedata.normalize('NFKC', value).casefold())


def matches_exercise(exercise, *, query=None, body_part=None):
    metadata = selection_metadata(exercise)
    if body_part and body_part not in metadata['body_parts']:
        return False
    text = normalize_query(' '.join([exercise.name_zh, *metadata['search_aliases'], *metadata['body_parts']]))
    return all(normalize_query(token) in text for token in (query or '').split())
