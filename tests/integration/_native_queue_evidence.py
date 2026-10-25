"""Exact original-input evidence for native queue probes, not model echoes."""


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
