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


def recovery_records():
    retained = [{'id': 'q'+str(n), 'clientUserMessageId': 'client'+str(n),
                 'input': [{'type': 'text', 'text': text, 'text_elements': []}]}
                for n, text in enumerate(['ONE', 'TWO'], 1)]
    human = {'id': 'human-item', 'type': 'userMessage', 'clientId': 'human-client',
             'content': [{'type': 'text', 'text': 'HUMAN', 'text_elements': []}]}
    before = {'thread': {'id': 'canonical', 'turns': [
        {'id': 'human-turn', 'status': 'inProgress', 'items': [copy.deepcopy(human)]}]}}
    after = copy.deepcopy(before)
    for n, receipt in enumerate(retained, 1):
        after['thread']['turns'].append({'id': 'turn'+str(n), 'status': 'completed', 'items': [
            {'id': 'item'+str(n), 'type': 'userMessage', 'clientId': receipt['clientUserMessageId'],
             'content': copy.deepcopy(receipt['input'])}]})
    requests = [{'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}]}
                for text in ['HUMAN', 'ONE', 'TWO']]
    return retained, before, after, requests


def test_recovery_binds_both_adds_original_human_and_each_provider_request():
    retained, before, after, requests = recovery_records()
    human = evidence.assert_recovery_pending(before, 'canonical', retained, copy.deepcopy(retained), 'HUMAN')
    evidence.assert_recovery_completed(after, 'canonical', retained, human, ['turn1', 'turn2'], requests, 'HUMAN')
    # Assistant echoes do not count as another original or another execution.
    after['thread']['turns'][0]['items'].append({'type': 'agentMessage', 'text': 'HUMAN ONE TWO'})
    evidence.assert_recovery_completed(after, 'canonical', retained, human, ['turn1', 'turn2'], requests, 'HUMAN')


@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'substitute_client', 'substitute_input',
                               'duplicate_add_id', 'duplicate_add_client', 'duplicate_add_input',
                               'wrong_human', 'extra_pending', 'extra_add_receipt'])
def test_recovery_precrash_requires_exact_intended_membership(bad):
    retained, before, _, _ = recovery_records()
    pending = copy.deepcopy(retained)
    if bad == 'missing': pending.pop()
    elif bad == 'duplicate': pending[1] = copy.deepcopy(pending[0])
    elif bad == 'substitute_client': pending[1]['clientUserMessageId'] = 'foreign'
    elif bad == 'substitute_input': pending[1]['input'] = copy.deepcopy(pending[0]['input'])
    elif bad == 'duplicate_add_id': retained[1]['id'] = retained[0]['id']
    elif bad == 'duplicate_add_client':
        retained[1]['clientUserMessageId'] = retained[0]['clientUserMessageId']
        pending[1]['clientUserMessageId'] = retained[0]['clientUserMessageId']
    elif bad == 'duplicate_add_input':
        retained[1]['input'] = copy.deepcopy(retained[0]['input'])
        pending[1]['input'] = copy.deepcopy(retained[0]['input'])
    elif bad == 'extra_pending': pending.append({'id': 'foreign', 'clientUserMessageId': 'foreign', 'input': []})
    elif bad == 'extra_add_receipt': retained.append(copy.deepcopy(retained[0]))
    else: before['thread']['turns'][0]['items'][0]['content'][0]['text'] = 'OTHER'
    with pytest.raises(AssertionError):
        evidence.assert_recovery_pending(before, 'canonical', retained, pending, 'HUMAN')


@pytest.mark.parametrize('bad', ['missing_two', 'duplicate_one', 'human_replay', 'human_replaced',
    'provider_human_replay', 'provider_one_twice', 'observed_turn_substitution', 'one_observed_twice',
    'same_execution_turn', 'changed_full_input', 'not_completed', 'same_execution_and_observer', 'extra_provider_human'])
def test_recovery_rejects_substitution_missing_inputs_and_human_replay(bad):
    retained, before, after, requests = recovery_records()
    human = evidence.assert_recovery_pending(before, 'canonical', retained, copy.deepcopy(retained), 'HUMAN')
    turns = ['turn1', 'turn2']
    if bad == 'missing_two': after['thread']['turns'].pop()
    elif bad == 'duplicate_one': after['thread']['turns'][2] = copy.deepcopy(after['thread']['turns'][1])
    elif bad == 'human_replay':
        replay = copy.deepcopy(after['thread']['turns'][0])
        replay['id'] = 'replayed-turn'
        replay['items'][0]['id'] = 'replayed-item'
        replay['items'][0]['clientId'] = 'new-human-client'
        after['thread']['turns'].append(replay)
    elif bad == 'human_replaced': after['thread']['turns'][0]['id'] = 'replayed-human-turn'
    elif bad == 'provider_human_replay': requests[2] = copy.deepcopy(requests[0])
    elif bad == 'provider_one_twice': requests[2] = copy.deepcopy(requests[1])
    elif bad == 'observed_turn_substitution': turns[1] = 'human-turn'
    elif bad == 'one_observed_twice': turns[1] = turns[0]
    elif bad == 'same_execution_turn': after['thread']['turns'][2]['id'] = 'turn1'
    elif bad == 'same_execution_and_observer':
        after['thread']['turns'][2]['id'] = 'turn1'
        turns = ['turn1', 'turn1']
    elif bad == 'extra_provider_human': requests.append(copy.deepcopy(requests[0]))
    elif bad == 'changed_full_input': after['thread']['turns'][2]['items'][0]['content'].append({'type': 'text', 'text': 'EXTRA'})
    else: after['thread']['turns'][2]['status'] = 'interrupted'
    with pytest.raises(AssertionError):
        evidence.assert_recovery_completed(after, 'canonical', retained, human, turns, requests, 'HUMAN')


def test_review_counterexample_one_one_plus_human_does_not_prove_two_retained_inputs():
    retained, before, after, requests = recovery_records()
    pending = [copy.deepcopy(retained[0]), copy.deepcopy(retained[0])]
    with pytest.raises(AssertionError): evidence.assert_recovery_pending(before, 'canonical', retained, pending, 'HUMAN')
    human = evidence.assert_recovery_pending(before, 'canonical', retained, copy.deepcopy(retained), 'HUMAN')
    after['thread']['turns'][2] = copy.deepcopy(after['thread']['turns'][0])
    after['thread']['turns'][2]['id'] = 'replay'
    after['thread']['turns'][2]['items'][0]['id'] = 'replay-input'
    requests[2] = copy.deepcopy(requests[0])
    with pytest.raises(AssertionError):
        evidence.assert_recovery_completed(after, 'canonical', retained, human, ['turn1', 'replay'], requests, 'HUMAN')
