"""Same source-backed bus/time renewable offers for dispatch and PRICE."""
import math

def window(energy,first,count):
    series=energy['renewable_bid_yuan_per_mwh'];result={}
    for bus in energy['buses']:
        values=[]
        for t in range(first,first+count):
            x=series[bus,t]
            if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x):
                raise ValueError('finite renewable bid required for bus/time '+str((bus,t)))
            values.append(float(x))
        result[str(bus)]=values
    return result

def verify_window(actual,energy,first,count):
    expected=window(energy,first,count)
    if actual!=expected:raise ValueError('PRICE renewable bids differ from same-source dispatch bus/time offers')
    return expected

def price_cost(power,curtail,explicit,bus,t,duration_h):
    return duration_h*(explicit['VRE_offers_by_tick'][str(bus)][t]*power+explicit['penalties']['renewable_curtailment']*curtail)
