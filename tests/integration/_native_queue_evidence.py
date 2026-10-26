"""Exact original-input evidence for native queue probes, not model echoes."""
import copy
import json


def assert_queued_original(history, thread_id, submission, turn_id, *, status='completed'):
    assert history['thread']['id'] == thread_id, 'history must belong to the canonical thread'
    matches = [(turn, item) for turn in history['thread']['turns'] for item in turn['items']
               if item.get('type') == 'userMessage'
               and item.get('clientId') == submission['clientUserMessageId']]
    assert len(matches) == 1, 'queued client identity must occur exactly once as original input'
    turn, item = matches[0]
    assert turn['id'] == turn_id, 'original input must belong to the observed turn'
    assert item['content'] == submission['input'], 'complete original queued input must match'
    assert turn['status'] == status, 'execution status must match the claimed outcome'


def assert_recovery_pending(history, thread_id, retained, pending, human_text):
    """Bind exact add receipts, pre-crash membership and original human input."""
    assert history.get('thread', {}).get('id') == thread_id, 'pre-crash canonical identity'
    assert len(retained) == 2, 'exactly two intended retained inputs'
    assert len({s['id'] for s in retained}) == 2, 'distinct retained queue identities'
    assert len({s['clientUserMessageId'] for s in retained}) == 2, 'distinct retained client identities'
    assert len({json.dumps(s['input'], sort_keys=True) for s in retained}) == 2, 'distinct retained inputs'
    assert len(pending) == 2, 'pre-crash queue must contain both intended inputs only'
    for receipt in retained:
        found = [q for q in pending if q['id'] == receipt['id']]
        assert len(found) == 1, 'each add receipt must occur exactly once before crash'
        assert found[0]['clientUserMessageId'] == receipt['clientUserMessageId'], 'pre-crash client association'
        assert found[0]['input'] == receipt['input'], 'pre-crash complete input association'
    originals = [(t['id'], entry) for t in history['thread']['turns'] for entry in t['items']
                 if entry.get('type') in ('userMessage',)]
    assert len(originals) == 1, 'one original human dispatch before crash'
    turn_id, item = originals[0]
    assert len(item['content']) == 1 and item['content'][0].get('type') == 'text', 'human input shape'
    assert item['content'][0]['text'] == human_text, 'bind the originally submitted human input'
    return turn_id, copy.deepcopy(item)


def assert_recovery_completed(history, thread_id, retained, human, turns, requests, human_text):
    """Each retained input executes on its own turn; human dispatch is not replayed."""
    originals = [(t['id'], entry) for t in history['thread']['turns'] for entry in t['items']
                 if entry.get('type') in ('userMessage',)]
    assert len(originals) == 3, 'no extra original input or human replay'
    original_human = [(tid, entry) for tid, entry in originals if entry['id'] == human[1]['id']]
    assert original_human == [human], 'same original human item and turn must survive'
    matched_turns = []
    for receipt in retained:
        found = [(tid, entry) for tid, entry in originals
                 if entry.get('clientId') == receipt['clientUserMessageId']]
        assert len(found) == 1, 'every intended retained input must occur exactly once'
        assert_queued_original(history, thread_id, receipt, found[0][0])
        matched_turns.append(found[0][0])
    assert len(turns) == 2 and len(set(turns)) == 2, 'two distinct observed completed turns'
    assert set(matched_turns) == set(turns), 'completed turns must be exactly the two retained inputs'
    assert human[0] not in matched_turns, 'human turn cannot substitute for retained work'
    assert len(requests) == 3, 'original human request plus exactly two retained requests'
    expected = [human_text] + [s['input'][0]['text'] for s in retained]
    for request, text in zip(requests, expected):
        users = [entry for entry in request['input'] if entry.get('role') == 'user']
        assert users, 'provider request must include its original user input'
        content = users[-1]['content']
        assert len(content) == 1 and content[0].get('type') == 'input_text', 'provider input shape'
        assert content[0]['text'] == text, 'provider request must belong to intended dispatch, without human replay'
