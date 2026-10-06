#!/bin/zsh
# Atalho para macOS: abra este arquivo por duplo clique no Finder.
cd "$(dirname "$0")"
exec .venv/bin/streamlit run dashboard.py
