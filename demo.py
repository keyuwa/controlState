#!/usr/bin/env python3
"""Synthetic control-policy experiment; not a HoneySat vulnerability."""
import json
from pathlib import Path


def run(name, protected, revoke):
    state = {'authorized': True, 'epoch': 1, 'heater': False}
    events = []
    def log(event, **details):
        events.append({'step': len(events) + 1, 'event': event,
                       'state': dict(state), **details})
    # Policy: revocation cancels all pending commands of this operator.
    command = {'operator': 'operator-a', 'operation': 'heater_on',
               'epoch': state['epoch']}
    if not state['authorized']:
        raise PermissionError('Submission denied')
    log('SUBMIT_AUTHORIZED', command=command)
    queue = [command]
    log('QUEUED_LINK_UNAVAILABLE')
    if revoke:
        state.update(authorized=False, epoch=state['epoch'] + 1)
        log('REVOKED_AND_SYNCHRONIZED_TO_EXECUTOR')
    log('LINK_RESTORED')
    pending = queue.pop(0)
    # Single-threaded demo: validation and effect form one executor step.
    if protected and (not state['authorized'] or pending['epoch'] != state['epoch']):
        log('REJECTED_STALE_AUTHORIZATION')
    else:
        state['heater'] = True
        log('EXECUTED_HEATER_ON')
    violation = state['heater'] and not state['authorized']
    return {'scenario': name, 'protected': protected,
            'policy_violation': violation, 'final_state': state, 'events': events}


def main():
    results = [run('normal', True, False), run('vulnerable', False, True),
               run('protected', True, True)]
    expected = [(True, False), (True, True), (False, False)]
    for result, (heater, violation) in zip(results, expected):
        if (result['final_state']['heater'], result['policy_violation']) != (heater, violation):
            raise RuntimeError('Unexpected experiment outcome')
        print('\n' + result['scenario'])
        for event in result['events']:
            print(f"  {event['step']}. {event['event']}")
        print(f"  heater={heater}, policy_violation={violation}")
    output = Path(__file__).with_name('results.json')
    output.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(f'\nPASS: 3 scenarios. Evidence: {output}')


if __name__ == '__main__':
    main()
