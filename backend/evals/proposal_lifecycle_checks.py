"""Adversarial old-card checks require an actually created replacement."""


def replaced_draft_references(bodies):
    if not bodies:
        return []
    latest = bodies[-1].get('proposal')
    if not isinstance(latest, dict) or not latest.get('id'):
        return []
    refs = {}
    for body in bodies[:-1]:
        prior = body.get('proposal')
        if isinstance(prior, dict) and prior.get('id') and prior['id'] != latest['id']:
            refs[prior['id']] = prior
    return list(refs.values())
