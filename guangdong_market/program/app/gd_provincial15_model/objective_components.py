"""Retain safety slack charges when replacing only the commercial energy bids."""
def append_slack_penalties(model,terms):
 # Curtailment and bids are already added by the caller. These terms are not.
 names=['network_penalty_cost','balance_penalty_cost']
 if hasattr(model,'reserve_penalty_cost'):names.append('reserve_penalty_cost')
 for name in names:terms.append(getattr(model,name))
 return names
