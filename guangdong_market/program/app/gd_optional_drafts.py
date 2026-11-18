"""Session drafts for storage and renewable editors, separate from saved inputs."""
from copy import deepcopy

def capture(state, item, fields):
 import streamlit as st
 st.session_state.setdefault(state,{})[item]={name:st.session_state[key] for name,key in fields.items()}

def discard(state,item,fields,accepted):
 import streamlit as st
 for name,key in fields.items():st.session_state[key]=accepted[name]
 st.session_state.setdefault(state,{}).pop(item,None)

def dirty(draft,accepted):
 return draft is not None and draft!=accepted

def stage(state,item,fields):
 """Read current widgets before validating; keep failed saves as drafts."""
 capture(state,item,fields)
 import streamlit as st
 return deepcopy(st.session_state[state][item])
