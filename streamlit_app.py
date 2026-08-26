"""Streamlit Cloud entry point.

Streamlit Community Cloud defaults its "Main file path" to ``streamlit_app.py``.
The real application lives in ``app.py``; this thin wrapper simply runs it, so a
deploy works whether the main file is set to ``app.py`` or ``streamlit_app.py``.
"""

from app import main

main()
