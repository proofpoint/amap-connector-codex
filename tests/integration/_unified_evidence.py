"""Exact, pure-data assertions for the synthetic unified protocol proof."""
import json


def assert_original_pointer(history, expected, accepted_turn):
    matches = []
    for turn in history['thread']['turns']:
        for item in turn.get('items', []):
            if (item.get('type') != 'functionCallOutput' or item.get('name') != 'amap_notice'
                    or item.get('namespace') != 'amap_connector'):
                continue
            try:
                pointer = json.loads(item.get('output', ''))
            except (TypeError, ValueError):
                continue
            if isinstance(pointer, dict) and pointer.get('event_id') == expected['event_id']:
                matches.append((turn['id'], pointer))
    assert len(matches) == 1, 'original pointer must occur exactly once'
    assert matches[0][0] == accepted_turn, 'pointer must belong to the accepted turn'
    assert matches[0][1] == expected, 'complete original pointer must be preserved'


def history_snapshot(history):
    thread = history['thread']
    # Serialize now so later mutation of an aliased object cannot change both
    # sides of the comparison. Include every item field, turn ID and ordering.
    return json.dumps({'thread_id': thread['id'], 'turns': thread['turns']},
                      sort_keys=True, separators=(',', ':'), allow_nan=False)


def assert_retained_history(before, after):
    assert history_snapshot(after) == before, 'reattachment must preserve exact ordered history'
