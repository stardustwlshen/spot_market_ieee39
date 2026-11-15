"""Different physical grids, same one-hour energy settlement boundary."""
from decimal import Decimal

def hour_ticks(hour, interval_minutes):
    if type(hour) is not int or not 0<=hour<24 or interval_minutes not in (5,15):
        raise ValueError('valid day hour and explicit 5/15 minute physical grid required')
    count=60//interval_minutes
    return list(range(hour*count,(hour+1)*count))

def hour_energy(samples, interval_minutes):
    if interval_minutes not in (5,15) or len(samples)!=60//interval_minutes:
        raise ValueError('complete physical hour required; do not mix DA and RT indices')
    values=[Decimal(str(x)) for x in samples]
    if not all(x.is_finite() for x in values):raise ValueError('finite physical power required')
    return sum(values,Decimal(0))*Decimal(interval_minutes)/60
