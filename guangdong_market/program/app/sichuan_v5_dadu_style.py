"""Local navigation styles for the five Dadu result tabs."""
import streamlit as st


def apply_dadu_style():
    st.html("""
        <style>
        body:has([role="tabpanel"]:not([hidden]) .st-key-dadu_run) [data-testid="stSkillsNudge"] {
            display: none;
        }
        .st-key-dadu_role_tabs [role="tablist"] [role="tab"] {
            min-height: 44px;
        }
        .st-key-dadu_role_tabs [role="tablist"] [role="tab"]:focus-visible {
            outline: 2px solid currentColor;
            outline-offset: -3px;
        }
        @media (max-width: 720px) {
            .st-key-dadu_role_tabs [role="tablist"] {
                max-width: 100%;
                overflow-x: auto;
                overscroll-behavior-x: contain;
                scroll-padding-inline: .25rem;
                scrollbar-width: thin;
            }
            .st-key-dadu_role_tabs [role="tablist"] [role="tab"] {
                flex: 0 0 auto;
                white-space: nowrap;
                padding-inline: .4rem;
            }
        }
        </style>
    """)
