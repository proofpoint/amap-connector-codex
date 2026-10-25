"""Offline negative controls for native queue execution evidence."""
import copy
import importlib.util
from pathlib import Path

import pytest

_path = Path(__file__).parent / 'integration/_native_queue_evidence.py'
_spec = importlib.util.spec_from_file_location('native_queue_evidence', _path)
evidence = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evidence)


def records():
    submission = {'id': 'queued1', 'clientUserMessageId': 'client1', 'input': [
        {'type': 'text', 'text': 'AMAP notice pointer. Follow the standing workflow: '
         '{"event_id":"event1","lane":"peer","notice_id":"notice1"}', 'text_elements': []}]}
    history = {'thread': {'id': 'canonical', 'turns': [
        {'id': 'turn1', 'status': 'completed', 'items': [
            {'type': 'userMessage', 'id': 'input1', 'clientId': 'client1',
             'content': copy.deepcopy(submission['input'])},
            {'type': 'agentMessage', 'id': 'output1', 'text': 'synthetic reply'}]}]}}
    return submission, history


def test_queued_original_matches_full_input_client_thread_turn_and_status():
    submission, history = records()
    evidence.assert_queued_original(history, 'canonical', submission, 'turn1')
    history['thread']['turns'][0]['status'] = 'interrupted'
    evidence.assert_queued_original(history, 'canonical', submission, 'turn1', status='interrupted')


@pytest.mark.parametrize('change', ['thread', 'client', 'role', 'turn', 'content', 'extra_content',
                                  'duplicate_item', 'duplicate_turn', 'status'])
def test_queued_original_refuses_inexact_or_ambiguous_evidence(change):
    submission, history = records()
    turn = history['thread']['turns'][0]
    item = turn['items'][0]
    if change == 'thread':
        history['thread']['id'] = 'foreign'
    elif change == 'client':
        item['clientId'] = 'another-client'
    elif change == 'role':
        item['type'] = 'agentMessage'
    elif change == 'turn':
        turn['id'] = 'unobserved-turn'
    elif change == 'content':
        item['content'][0]['text'] = item['content'][0]['text'].replace('"peer"', '"mail"')
    elif change == 'extra_content':
        item['content'].append({'type': 'text', 'text': 'extra instruction'})
    elif change == 'duplicate_item':
        turn['items'].append(copy.deepcopy(item))
    elif change == 'duplicate_turn':
        history['thread']['turns'].append(copy.deepcopy(turn))
    else:
        turn['status'] = 'interrupted'
    with pytest.raises(AssertionError):
        evidence.assert_queued_original(history, 'canonical', submission, 'turn1')
